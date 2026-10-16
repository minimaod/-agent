"""在问题改写完成后尝试复用已执行成功的 SQL。"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def check_semantic_cache(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):
    miss = {
        "is_cache_hit": False,
        "cached_sql": None,
        "cache_meta": None,
        "cache_validation_failed": False,
    }
    if state["rewrite_result"].intent not in ("follow_up", "new_topic"):
        return miss
    cache = runtime.context.get("semantic_cache_service")
    if cache is None:
        return miss
    try:
        record = await cache.search_cache(state["query"])
    except Exception as exc:
        logger.warning("语义缓存查询失败，继续正常问数：{}", exc)
        return miss
    if record is None:
        logger.info("语义缓存未命中")
        return miss

    logger.info("语义缓存命中：id={} score={}", record["id"], record["score"])
    runtime.stream_writer(
        {
            "type": "progress",
            "step": "cache_hit",
            "status": "success",
            "message": "命中高频问数语义缓存，直接复用已验证 SQL，继续校验与执行...",
        }
    )
    return {
        "is_cache_hit": True,
        "cached_sql": record["sql"],
        "sql": record["sql"],
        "cache_meta": record["meta"],
        "cache_validation_failed": False,
        "error": None,
    }
