"""离线验证缓存存取、真实图拓扑和 SSE；不连接模型或业务数据库。"""

import copy
import importlib
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import app_config
from app.entities.query_rewrite import QueryRewriteResult
from app.services.semantic_cache_service import SemanticCacheService


def vector(score=1.0):
    return [score, math.sqrt(1 - score**2)] + [0.0] * (
        app_config.qdrant.embedding_size - 2
    )


class SemanticCacheServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncQdrantClient(":memory:")
        self.embedding = SimpleNamespace(aembed_query=AsyncMock(return_value=vector()))
        self.cache = SemanticCacheService(self.client, self.embedding)

    async def asyncTearDown(self):
        await self.client.close()

    async def test_collection_and_round_trip(self):
        self.assertIsNone(await self.cache.search_cache("华北销售额"))
        await self.cache.save_cache(
            " 华北销售额 ", " SELECT 1 ", {"intent": "new_topic"}
        )
        record = await self.cache.search_cache("华北销售总额")
        self.assertEqual(record["sql"], "SELECT 1")
        self.assertEqual(record["query_text"], "华北销售额")
        self.assertEqual(record["meta"], {"intent": "new_topic"})
        info = await self.client.get_collection(self.cache.collection_name)
        self.assertEqual(
            info.config.params.vectors.size, app_config.qdrant.embedding_size
        )
        self.assertEqual(info.config.params.vectors.distance, models.Distance.COSINE)
        await self.cache.save_cache("华北销售额", "SELECT 2")
        count = await self.client.count(self.cache.collection_name)
        self.assertEqual(count.count, 1)
        self.assertEqual(
            (await self.cache.search_cache("华北销售额"))["sql"], "SELECT 2"
        )

    async def test_threshold_and_highest_score(self):
        await self.cache.save_cache("已缓存问题", "SELECT 1")
        self.embedding.aembed_query.return_value = vector(0.949)
        self.assertIsNone(await self.cache.search_cache("相似问题"))
        self.assertIsNotNone(await self.cache.search_cache("相似问题", 0.94))
        self.embedding.aembed_query.return_value = vector(0.951)
        self.assertIsNotNone(await self.cache.search_cache("高于阈值"))
        # 更高分的 SQL 应当胜出。
        await self.cache.save_cache("最高分问题", "SELECT 2")
        self.assertEqual(
            (await self.cache.search_cache("最高分问题"))["sql"], "SELECT 2"
        )

    async def test_exact_threshold_and_malformed_payload(self):
        self.cache.client = SimpleNamespace(
            query_points=AsyncMock(
                return_value=SimpleNamespace(
                    points=[
                        SimpleNamespace(
                            id="point", score=0.95, payload={"sql": "SELECT 1"}
                        )
                    ]
                )
            )
        )
        with patch.object(self.cache, "ensure_collection", new=AsyncMock()):
            self.assertIsNotNone(await self.cache.search_cache("等于阈值"))
            point = self.cache.client.query_points.return_value.points[0]
            point.score = 0.949
            self.assertIsNone(await self.cache.search_cache("服务端返回低分"))
            point.score = 1.0
            point.payload = {"sql": "  "}
            self.assertIsNone(await self.cache.search_cache("无有效 SQL"))

    async def test_wrong_dimensions_and_empty_inputs(self):
        self.embedding.aembed_query.return_value = [1.0]
        with self.assertRaisesRegex(ValueError, "向量维度"):
            await self.cache.save_cache("问题", "SELECT 1")
        self.assertIsNone(await self.cache.search_cache(" "))
        with self.assertRaises(ValueError):
            await self.cache.save_cache("问题", " ")

    async def test_incompatible_existing_collection_is_not_recreated(self):
        await self.client.create_collection(
            self.cache.collection_name,
            vectors_config=models.VectorParams(size=2, distance=models.Distance.DOT),
        )
        with self.assertRaisesRegex(ValueError, "配置"):
            await self.cache.search_cache("问题")
        info = await self.client.get_collection(self.cache.collection_name)
        self.assertEqual(info.config.params.vectors.size, 2)

    async def test_concurrent_collection_creation_and_real_failure(self):
        self.cache.client = SimpleNamespace(
            collection_exists=AsyncMock(side_effect=[False, True]),
            create_collection=AsyncMock(side_effect=RuntimeError("already exists")),
            get_collection=AsyncMock(
                return_value=SimpleNamespace(
                    config=SimpleNamespace(
                        params=SimpleNamespace(
                            vectors=models.VectorParams(
                                size=app_config.qdrant.embedding_size,
                                distance=models.Distance.COSINE,
                            )
                        )
                    )
                )
            ),
        )
        await self.cache.ensure_collection()
        self.cache.client.collection_exists.side_effect = [False, False]
        with self.assertRaises(RuntimeError):
            await self.cache.ensure_collection()


class SemanticCacheWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 禁止测试初始化文件日志；所有外部调用均使用模拟对象。
        with patch.object(app_config.logging.file, "enable", False):
            self.graph_module = importlib.import_module("app.agent.graph")
            self.query_module = importlib.import_module("app.services.query_service")
            self.rewrite_module = importlib.import_module(
                "app.agent.nodes.rewrite_query"
            )
        self.record = {
            "id": "cached-point",
            "score": 0.99,
            "sql": "SELECT 1",
            "meta": {"source": "test"},
        }
        self.cache = SimpleNamespace(
            search_cache=AsyncMock(return_value=self.record), save_cache=AsyncMock()
        )
        self.dw = SimpleNamespace(
            validate=AsyncMock(), run=AsyncMock(return_value=[{"total": 42}])
        )
        self.visited = []
        # 复用生产图的全部边和条件路由，仅替换昂贵的检索与模型节点。
        builder = copy.copy(self.graph_module.graph_builder)
        builder.compiled = False
        builder.nodes = builder.nodes.copy()
        retained = {
            "rewrite_query",
            "check_semantic_cache",
            "validate_sql",
            "run_sql",
            "respond_query",
        }
        for name in list(builder.nodes):
            if name in retained:
                continue

            def stub(node_name):
                async def execute(state):
                    self.visited.append(node_name)
                    if node_name == "generate_sql":
                        return {"sql": "SELECT 2"}
                    if node_name == "correct_sql":
                        return {"sql": "SELECT 3"}
                    return {}

                return execute

            del builder.nodes[name]
            builder.add_node(name, stub(name))
        self.graph = builder.compile()
        self.rewrite = QueryRewriteResult(
            intent="follow_up", rewritten_query="统计华北完整销售额"
        )

    async def run_query(self, intent=None, expect_success=True):
        if intent is not None:
            self.rewrite = QueryRewriteResult(intent=intent, message="请补充统计条件")
        service = self.query_module.QueryService(
            meta_mysql_repository=None,
            embedding_client=None,
            dw_mysql_repository=self.dw,
            column_qdrant_repository=None,
            metric_qdrant_repository=None,
            value_es_repository=None,
            semantic_cache_service=self.cache,
        )
        self.session_id = str(uuid4())
        self.states = []
        graph = self.graph

        async def recording_stream(**kwargs):
            async for mode, chunk in graph.astream(**kwargs):
                if mode == "values":
                    self.states.append(chunk)
                yield mode, chunk

        with (
            patch.object(
                self.query_module, "graph", SimpleNamespace(astream=recording_stream)
            ),
            patch.object(
                self.rewrite_module,
                "understand_query",
                new=AsyncMock(return_value=self.rewrite),
            ),
        ):
            chunks = [
                chunk async for chunk in service.query("那华北呢？", self.session_id)
            ]
        self.events = [json.loads(chunk.split("data: ", 1)[1]) for chunk in chunks]
        errors = [event for event in self.events if event["type"] == "error"]
        if expect_success:
            self.assertFalse(errors)
        else:
            self.assertEqual(len(errors), 1)
        return chunks

    async def test_hit_skips_retrieval_generation_but_validates_and_runs(self):
        chunks = await self.run_query()
        self.cache.search_cache.assert_awaited_once_with("统计华北完整销售额")
        self.assertEqual(self.visited, [])
        self.dw.validate.assert_awaited_once_with("SELECT 1")
        self.dw.run.assert_awaited_once_with("SELECT 1")
        self.cache.save_cache.assert_not_awaited()
        self.assertTrue(self.states[-1]["is_cache_hit"])
        self.assertEqual(self.states[-1]["cached_sql"], "SELECT 1")
        self.assertTrue(
            any(chunk.startswith("event: progress\ndata:") for chunk in chunks)
        )
        self.assertEqual(
            len([e for e in self.events if e.get("step") == "cache_hit"]), 1
        )
        history = self.query_module.conversation_memory_store.get_history(
            self.session_id
        )
        self.assertEqual(history[-1].sql, "SELECT 1")

    async def test_miss_runs_full_chain_and_saves_rewritten_query(self):
        self.cache.search_cache.return_value = None
        await self.run_query()
        for name in (
            "extract_keywords",
            "recall_column",
            "recall_metric",
            "recall_value",
            "generate_sql",
        ):
            self.assertIn(name, self.visited)
        self.cache.save_cache.assert_awaited_once_with(
            "统计华北完整销售额", "SELECT 2", meta={"intent": "follow_up"}
        )

    async def test_new_topic_uses_cache(self):
        self.rewrite.intent = "new_topic"
        await self.run_query()
        self.cache.search_cache.assert_awaited_once()
        self.dw.run.assert_awaited_once_with("SELECT 1")

    async def test_successful_query_populates_cache_for_next_request(self):
        client = AsyncQdrantClient(":memory:")
        embedding = SimpleNamespace(aembed_query=AsyncMock(return_value=vector()))
        self.cache = SemanticCacheService(client, embedding)
        try:
            await self.run_query()
            self.assertIn("generate_sql", self.visited)
            self.assertFalse(self.states[-1]["is_cache_hit"])
            self.visited.clear()
            await self.run_query()
            self.assertEqual(self.visited, [])
            self.assertTrue(self.states[-1]["is_cache_hit"])
            self.assertEqual(self.states[-1]["sql"], "SELECT 2")
            self.assertEqual(self.dw.run.await_count, 2)
        finally:
            await client.close()

    async def test_clarify_and_out_of_scope_bypass_cache(self):
        for intent in ("clarify", "out_of_scope"):
            await self.run_query(intent)
        self.cache.search_cache.assert_not_awaited()
        self.cache.save_cache.assert_not_awaited()
        self.dw.validate.assert_not_awaited()
        self.dw.run.assert_not_awaited()
        self.assertEqual(self.visited, [])

    async def test_lookup_failure_falls_back_and_save_failure_preserves_result(self):
        self.cache.search_cache.side_effect = RuntimeError("cache offline")
        self.cache.save_cache.side_effect = RuntimeError("write failed")
        await self.run_query()
        self.assertIn("generate_sql", self.visited)
        self.assertTrue(any(e["type"] == "result" for e in self.events))
        history = self.query_module.conversation_memory_store.get_history(
            self.session_id
        )
        self.assertEqual(history[-1].sql, "SELECT 2")

    async def test_invalid_cached_sql_falls_back_without_missing_context(self):
        self.dw.validate.side_effect = [RuntimeError("unknown column"), None]
        await self.run_query()
        self.assertIn("extract_keywords", self.visited)
        self.assertNotIn("correct_sql", self.visited)
        self.dw.run.assert_awaited_once_with("SELECT 2")
        self.assertFalse(self.states[-1]["is_cache_hit"])
        self.cache.save_cache.assert_awaited_once()

    async def test_normal_validation_failure_keeps_original_correction_route(self):
        self.cache.search_cache.return_value = None
        self.dw.validate.side_effect = RuntimeError("syntax error")
        await self.run_query()
        self.assertIn("correct_sql", self.visited)
        self.dw.run.assert_awaited_once_with("SELECT 3")
        self.assertEqual(self.cache.save_cache.await_args.args[1], "SELECT 3")

    async def test_execution_failure_does_not_save_or_append_history(self):
        self.cache.search_cache.return_value = None
        self.dw.run.side_effect = RuntimeError("query failed")
        await self.run_query(expect_success=False)
        self.cache.save_cache.assert_not_awaited()
        history = self.query_module.conversation_memory_store.get_history(
            self.session_id
        )
        self.assertEqual(history, [])


if __name__ == "__main__":
    unittest.main()
