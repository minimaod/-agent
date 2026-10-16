"""问数问题理解与改写节点。"""

import json

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.entities.conversation import SuccessfulTurn
from app.entities.query_rewrite import QueryRewriteResult
from app.prompt.prompt_loader import load_prompt


rewrite_output_parser = PydanticOutputParser(
    pydantic_object=QueryRewriteResult,
)

rewrite_prompt = PromptTemplate(
    template=load_prompt("rewrite_query"),
    input_variables=["history", "raw_query"],
    partial_variables={
        "format_instructions": rewrite_output_parser.get_format_instructions(),
    },
)

rewrite_chain = rewrite_prompt | llm | rewrite_output_parser


def format_history(history: list[SuccessfulTurn]) -> str:
    items = []
    for turn in history:
        items.append({"raw_query": turn.raw_query, "query": turn.query})
    return json.dumps(items, ensure_ascii=False)


async def understand_query(raw_query: str, history_text: str) -> QueryRewriteResult:
    """调用现有模型，返回本轮问题的理解结果。"""
    result = await rewrite_chain.ainvoke(
        {"raw_query": raw_query, "history": history_text}
    )
    return result


async def rewrite_query(
    state: DataAgentState,
    runtime: Runtime[DataAgentContext],
):
    """读取本轮状态，返回问题理解结果和待查询问题。"""
    writer = runtime.stream_writer
    step = "理解问题"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        history_text = format_history(state["history"])
        result = await understand_query(state["raw_query"], history_text)

        query = ""
        if result.intent in ("follow_up", "new_topic"):
            query = result.rewritten_query.strip()
            if not query:
                raise ValueError("问题理解结果缺少可查询的完整问题")

        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "rewrite_result": result,
            "query": query,
        }
    except Exception:
        writer({"type": "progress", "step": step, "status": "error"})
        raise
