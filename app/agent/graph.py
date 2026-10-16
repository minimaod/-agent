"""
电商问数 Agent 图编排

使用 LangGraph 把问数智能体的各个节点串成一条可观测的执行链路
当前链路已经落地关键词抽取和多路召回，字段和指标走 Qdrant 向量检索，字段取值走 ES 全文检索
先改写完整问题并检查语义缓存；命中后直接校验并执行 SQL。
未命中则抽取关键词，并行召回字段、字段取值和指标，合并、过滤、补齐上下文后生成 SQL。
缓存 SQL 校验失败会回到完整链路，普通 SQL 校验失败则按原有逻辑校正一次。
"""

import asyncio

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.context import DataAgentContext
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.check_semantic_cache import check_semantic_cache
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.respond_query import respond_query
from app.agent.nodes.rewrite_query import rewrite_query
from app.agent.nodes.run_sql import run_sql
from app.agent.nodes.validate_sql import validate_sql
from app.agent.state import DataAgentState
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.semantic_cache_service import SemanticCacheService


def route_after_rewrite(state: DataAgentState) -> str:
    """根据问题理解结果，选择继续问数或仅回复消息。"""
    intent = state["rewrite_result"].intent

    if intent in ("follow_up", "new_topic"):
        return "continue_query"

    return "reply_only"


def route_after_validation(state: DataAgentState) -> str:
    """过期缓存缺少校正所需的上下文，须回到完整检索链路。"""
    if state.get("cache_validation_failed", False):
        return "extract_keywords"
    return "run_sql" if state["error"] is None else "correct_sql"


# StateGraph 声明整张图使用的状态结构和运行时上下文结构
graph_builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

# 注册节点：每个节点负责问数链路中的一个清晰步骤
graph_builder.add_node("rewrite_query", rewrite_query)
graph_builder.add_node("check_semantic_cache", check_semantic_cache)
graph_builder.add_node("respond_query", respond_query)
graph_builder.add_node("extract_keywords", extract_keywords)
graph_builder.add_node("recall_column", recall_column)
graph_builder.add_node("recall_value", recall_value)
graph_builder.add_node("recall_metric", recall_metric)
graph_builder.add_node("merge_retrieved_info", merge_retrieved_info)
graph_builder.add_node("filter_metric", filter_metric)
graph_builder.add_node("filter_table", filter_table)
graph_builder.add_node("add_extra_context", add_extra_context)
graph_builder.add_node("generate_sql", generate_sql)
graph_builder.add_node("validate_sql", validate_sql)
graph_builder.add_node("correct_sql", correct_sql)
graph_builder.add_node("run_sql", run_sql)

# 每轮先理解问题，再根据意图选择后续路径
graph_builder.add_edge(START, "rewrite_query")

graph_builder.add_conditional_edges(
    source="rewrite_query",
    path=route_after_rewrite,
    path_map={
        "continue_query": "check_semantic_cache",
        "reply_only": "respond_query",
    },
)

graph_builder.add_conditional_edges(
    source="check_semantic_cache",
    path=lambda state: "hit" if state.get("is_cache_hit", False) else "miss",
    path_map={"hit": "validate_sql", "miss": "extract_keywords"},
)

# 澄清或范围提示发送后，结束本轮
graph_builder.add_edge("respond_query", END)

# 关键词抽取后并行进入三类召回，分别面向字段 字段值和业务指标
graph_builder.add_edge("extract_keywords", "recall_column")
graph_builder.add_edge("extract_keywords", "recall_value")
graph_builder.add_edge("extract_keywords", "recall_metric")

# 三路召回都完成后，再进入统一的信息合并节点
graph_builder.add_edge("recall_column", "merge_retrieved_info")
graph_builder.add_edge("recall_value", "merge_retrieved_info")
graph_builder.add_edge("recall_metric", "merge_retrieved_info")

# 合并后的候选信息继续拆成表过滤和指标过滤两条线
graph_builder.add_edge("merge_retrieved_info", "filter_table")
graph_builder.add_edge("merge_retrieved_info", "filter_metric")

# 表和指标都过滤完成后，统一补充生成 SQL 所需的上下文
graph_builder.add_edge("filter_table", "add_extra_context")
graph_builder.add_edge("filter_metric", "add_extra_context")
graph_builder.add_edge("add_extra_context", "generate_sql")
graph_builder.add_edge("generate_sql", "validate_sql")

# SQL 校验通过就直接执行，校验失败则先进入修正节点
graph_builder.add_conditional_edges(
    source="validate_sql",
    path=route_after_validation,
    path_map={
        "run_sql": "run_sql",
        "correct_sql": "correct_sql",
        "extract_keywords": "extract_keywords",
    },
)
graph_builder.add_edge("correct_sql", "run_sql")
graph_builder.add_edge("run_sql", END)

# 编译后的 graph 是对外使用的 Agent 执行入口
graph = graph_builder.compile()

# print(graph.get_graph().draw_mermaid())

if __name__ == "__main__":

    async def test():
        """本地调试关键词抽取和字段 指标 取值三路召回链路"""

        # 多路召回和上下文补全会访问 Qdrant、Embedding、ES、Meta MySQL 和 DW MySQL
        qdrant_client_manager.init()
        embedding_client_manager.init()
        es_client_manager.init()
        meta_mysql_client_manager.init()
        dw_mysql_client_manager.init()

        # Meta MySQL 用来补齐元数据，DW MySQL 用来读取数据库方言和版本
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            meta_mysql_repository = MetaMySQLRepository(meta_session)
            dw_mysql_repository = DWMySQLRepository(dw_session)

            # 字段和指标分别使用不同 Qdrant collection，取值检索使用 ES index
            column_qdrant_repository = ColumnQdrantRepository(
                qdrant_client_manager.client
            )
            metric_qdrant_repository = MetricQdrantRepository(
                qdrant_client_manager.client
            )
            value_es_repository = ValueESRepository(es_client_manager.client)

            # 调试入口也要提供改写节点需要的原话和历史
            raw_query = "统计华北地区的销售总额"
            state = DataAgentState(
                session_id="local-debug",
                raw_query=raw_query,
                query=raw_query,
                history=[],
                query_succeeded=False,
                is_cache_hit=False,
                cached_sql=None,
                cache_validation_failed=False,
            )
            context = DataAgentContext(
                column_qdrant_repository=column_qdrant_repository,
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=metric_qdrant_repository,
                value_es_repository=value_es_repository,
                meta_mysql_repository=meta_mysql_repository,
                dw_mysql_repository=dw_mysql_repository,
                semantic_cache_service=SemanticCacheService(
                    qdrant_client_manager.client, embedding_client_manager.client
                ),
            )

            # stream_mode="custom" 会接收各节点通过 runtime.stream_writer 写出的进度信息
            async for chunk in graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                print(chunk)

        # 关闭显式创建的异步客户端，避免本地调试时连接资源悬挂
        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()

    asyncio.run(test())
