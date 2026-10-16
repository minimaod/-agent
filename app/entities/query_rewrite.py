"""本轮问题理解与改写的结果定义。"""

from typing import Literal

from pydantic import BaseModel


class QueryRewriteResult(BaseModel):
    """本轮问题理解与改写的结果。"""

    intent: Literal["follow_up", "new_topic", "clarify", "out_of_scope"]
    rewritten_query: str = ""
    message: str = ""
