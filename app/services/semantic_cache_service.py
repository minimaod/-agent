"""复用现有 Embedding 和 Qdrant，保存完整问题与已执行成功的 SQL。"""

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from langchain_huggingface import HuggingFaceEndpointEmbeddings
from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import app_config


class SemanticCacheService:
    """缓存 SQL 而非查询结果；命中后仍须校验并访问数仓。"""

    collection_name = "chatbi_semantic_cache"

    def __init__(
        self,
        client: AsyncQdrantClient,
        embedding_client: HuggingFaceEndpointEmbeddings,
    ):
        self.client = client
        self.embedding_client = embedding_client

    async def ensure_collection(self):
        """按现有向量维度创建集合；兼容多个请求同时首次创建。"""
        if not await self.client.collection_exists(self.collection_name):
            try:
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=app_config.qdrant.embedding_size,
                        distance=models.Distance.COSINE,
                    ),
                )
            except Exception:
                # 只容忍其他请求已经创建成功的情况，连接等故障继续上抛。
                if not await self.client.collection_exists(self.collection_name):
                    raise

        info = await self.client.get_collection(self.collection_name)
        vectors = info.config.params.vectors
        if (
            not isinstance(vectors, models.VectorParams)
            or vectors.size != app_config.qdrant.embedding_size
            or vectors.distance != models.Distance.COSINE
        ):
            raise ValueError("语义缓存集合的维度或距离配置与当前 Embedding 不一致")

    async def _embed(self, query_text: str) -> list[float]:
        vector = await self.embedding_client.aembed_query(query_text)
        if len(vector) != app_config.qdrant.embedding_size:
            raise ValueError("Embedding 返回的向量维度与 Qdrant 配置不一致")
        return vector

    async def search_cache(
        self, query_text: str, score_threshold: float = 0.95
    ) -> dict | None:
        """返回达到阈值的最高分记录；缺少有效 SQL 的记录视为未命中。"""
        query_text = query_text.strip()
        if not query_text:
            return None
        await self.ensure_collection()
        vector = await self._embed(query_text)
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=1,
            score_threshold=score_threshold,
            with_payload=True,
            with_vectors=False,
        )
        if not result.points:
            return None
        point = result.points[0]
        payload = point.payload or {}
        sql = payload.get("sql")
        if point.score < score_threshold or not isinstance(sql, str) or not sql.strip():
            return None
        return {
            "id": str(point.id),
            "query_text": payload.get("query_text", ""),
            "sql": sql.strip(),
            "meta": payload.get("meta") or {},
            "score": point.score,
        }

    async def save_cache(self, query_text: str, sql: str, meta: dict | None = None):
        """调用方必须在 SQL 执行成功后调用；相同完整问题更新同一个点。"""
        query_text, sql = query_text.strip(), sql.strip()
        if not query_text or not sql:
            raise ValueError("语义缓存的问题和 SQL 不能为空")
        await self.ensure_collection()
        vector = await self._embed(query_text)
        await self.client.upsert(
            collection_name=self.collection_name,
            wait=True,
            points=[
                models.PointStruct(
                    id=str(
                        uuid5(NAMESPACE_URL, f"{self.collection_name}:{query_text}")
                    ),
                    vector=vector,
                    payload={
                        "query_text": query_text,
                        "sql": sql,
                        "meta": meta or {},
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            ],
        )
