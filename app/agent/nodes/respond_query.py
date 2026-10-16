"""发送澄清问题或电商问数范围提示。"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState


def respond_query(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
):
    """将已有理解结果中的提示发送为消息事件。"""
    result = state["rewrite_result"]

    if result.intent not in ("clarify", "out_of_scope"):
        raise ValueError("回复节点只处理澄清和范围提示")

    message = result.message.strip()
    if not message:
        raise ValueError("问题理解结果缺少回复消息")

    runtime.stream_writer({
        "type": "message",
        "intent": result.intent,
        "message": message,
    })
    return {}
