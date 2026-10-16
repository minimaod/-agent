"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建初始 State、组装 Runtime Context、消费 graph.astream 的流式输出，
并统一包装成 SSE 文本返回给路由层。
"""

import json
from uuid import uuid4

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.core.log import logger
from app.entities.conversation import SuccessfulTurn
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.conversation_memory import conversation_memory_store
from app.services.semantic_cache_service import SemanticCacheService


class QueryService:
    """封装一次问数查询所需的业务编排逻辑"""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
        semantic_cache_service: SemanticCacheService | None = None,
    ):
        # MySQL 仓储分别负责元数据补全和真实数仓环境信息读取
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository

        # 召回链路依赖的向量检索、Embedding 和全文检索能力由依赖层注入
        self.embedding_client = embedding_client
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository
        self.semantic_cache_service = (
            semantic_cache_service
            if semantic_cache_service is not None
            else SemanticCacheService(column_qdrant_repository.client, embedding_client)
        )

    async def query(self, query: str, session_id: str | None = None):
        """执行一次问数工作流，并逐段产出 SSE 消息"""

        session_id = session_id or str(uuid4())
        # State 只放会被图节点读写和合并的业务数据，外部工具对象不塞进 State
        state = DataAgentState(
            raw_query=query,
            session_id=session_id,
            query=query,
            history=conversation_memory_store.get_history(session_id),
            query_succeeded=False,
            is_cache_hit=False,
            cached_sql=None,
            cache_validation_failed=False,
        )
        # Context 保存本次图执行需要复用的外部依赖，节点通过 runtime.context 读取
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
            semantic_cache_service=self.semantic_cache_service,
        )
        understanding_completed = False
        try:
            # 先告知客户端本轮使用的会话编号
            session_event = {"type": "session", "session_id": session_id}
            yield f"data: {json.dumps(session_event, ensure_ascii=False)}\n\n"
            final_state = None
            new_topic_started = False
            # 同时接收节点消息和内部 State，保留最后一份状态供成功记录使用
            async for mode, chunk in graph.astream(
                input=state,
                context=context,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    event_prefix = (
                        "event: progress\n"
                        if chunk.get("step") in ("cache_hit", "cache_fallback")
                        else ""
                    )
                    yield f"{event_prefix}data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
                elif mode == "values":
                    final_state = chunk
                    rewrite_result = chunk.get("rewrite_result")

                    if rewrite_result is not None:
                        understanding_completed = True

                    if (
                        not new_topic_started
                        and rewrite_result is not None
                        and rewrite_result.intent == "new_topic"
                    ):
                        conversation_memory_store.start_new_topic(session_id)
                        new_topic_started = True

            if final_state is not None and final_state.get("query_succeeded", False):
                turn = SuccessfulTurn(
                    raw_query=final_state["raw_query"],
                    query=final_state["query"],
                    sql=final_state["sql"],
                )
                conversation_memory_store.append_success(session_id, turn)
                if not final_state.get("is_cache_hit", False):
                    try:
                        # await 异步写入，确保请求正常结束前完成，避免后台任务丢失。
                        await self.semantic_cache_service.save_cache(
                            final_state["query"],
                            final_state["sql"],
                            meta={"intent": final_state["rewrite_result"].intent},
                        )
                    except Exception as exc:
                        logger.warning("SQL 执行成功，但语义缓存写入失败：{}", exc)
        except Exception as e:
            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
        finally:
            if not understanding_completed:
                conversation_memory_store.start_new_topic(session_id)
