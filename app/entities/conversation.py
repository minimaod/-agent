"""会话中成功查询轮次的数据定义。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SuccessfulTurn:
    """一轮成功查询留下的记录。"""

    raw_query: str
    query: str
    sql: str