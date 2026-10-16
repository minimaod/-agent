"""
电商问数 Agent 状态定义

State 是 LangGraph 各节点之间传递和更新的共享数据
本章在用户原始问题之外，新增关键词列表和三路召回结果
并把召回到的实体整理成后续提示词更容易消费的表信息和指标信息
SQL 生成闭环会继续写入候选 SQL 以及校验错误信息，用于控制校正或执行分支
"""

from typing import NotRequired, TypedDict

from app.entities.column_info import ColumnInfo
from app.entities.conversation import SuccessfulTurn
from app.entities.metric_info import MetricInfo
from app.entities.query_rewrite import QueryRewriteResult
from app.entities.value_info import ValueInfo


class MetricInfoState(TypedDict):
    """面向 SQL 生成提示词的指标信息"""

    name: str
    description: str
    # 指标依赖的字段 id，用来提示模型不要脱离业务口径随意计算
    relevant_columns: list[str]
    alias: list[str]


class ColumnInfoState(TypedDict):
    """表上下文中的字段信息"""

    name: str
    type: str
    role: str
    # 字段真实样例值，尤其用于辅助 where 条件里的枚举值选择
    examples: list
    description: str
    alias: list[str]


class TableInfoState(TypedDict):
    """SQL 生成阶段真正传给模型的表结构上下文"""

    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class DateInfoState(TypedDict):
    """SQL 生成阶段使用的当前日期上下文"""

    date: str
    weekday: str
    quarter: str


class DBInfoState(TypedDict):
    """SQL 生成阶段使用的数据库环境信息"""

    dialect: str
    version: str


class DataAgentState(TypedDict):
    """一次问数链路中的核心状态"""

    session_id: str  # 本轮问题所属的会话编号
    raw_query: str  # 用户本轮的原始输入
    query: str  # 交给后续问数节点处理的问题
    history: list[SuccessfulTurn]  # 本轮可参考的成功历史
    rewrite_result: NotRequired[QueryRewriteResult]  # 问题理解节点完成后写入的结果
    keywords: list[str]  # 抽取的关键词
    retrieved_column_infos: list[ColumnInfo]  # 检索到的字段信息
    retrieved_metric_infos: list[MetricInfo]  # 检索到的指标信息
    retrieved_value_infos: list[ValueInfo]  # 检索到的取值信息

    table_infos: list[TableInfoState]  # 合并和补齐后的表结构上下文
    metric_infos: list[MetricInfoState]  # 合并后的指标上下文
    date_info: DateInfoState  # 当前日期 星期和季度信息
    db_info: DBInfoState  # 数据库方言和版本信息

    sql: str  # 生成或校正后的SQL
    query_succeeded: bool  # 本轮 SQL 是否执行成功

    # TypedDict 不支持字段默认值，由查询入口初始化，缓存节点每轮重新赋值。
    is_cache_hit: NotRequired[bool]  # 默认 False
    cached_sql: NotRequired[str | None]  # 默认 None
    cache_meta: NotRequired[dict | None]
    cache_validation_failed: NotRequired[bool]

    error: str  # 校验SQL时出现的错误信息
