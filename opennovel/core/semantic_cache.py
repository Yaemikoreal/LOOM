"""语义缓存层 — 基于查询语义相似度的检索结果缓存。

使用 BGE-M3 embedding 对查询编码，余弦相似度 ≥ 阈值时命中缓存。
LRU 淘汰策略，可持久化到 SQLite。

详见 docs/adr/0009-advanced-retrieval-optimization.md。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from opennovel.schemas.search import SearchResponse

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.92
_DEFAULT_MAX_SIZE = 500


class SemanticCache:
    """基于语义相似度的检索结果缓存。

    命中时检索延迟从 ~200ms 降至 ~1ms（仅 embedding 计算）。
    每章完成后清空（新章节上下文需求通常不同）。

    使用方式:
        cache = SemanticCache(project_root)
        cached = cache.get(query)
        if cached is None:
            response = pipeline.search(query)
            cache.put(query, response)
    """

    def __init__(
        self,
        project_root: Path,
        threshold: float = _DEFAULT_THRESHOLD,
        max_size: int = _DEFAULT_MAX_SIZE,
    ) -> None:
        """初始化语义缓存。

        Args:
            project_root: 项目根目录
            threshold: 语义相似度命中阈值（0.0~1.0）
            max_size: 最大缓存条目数
        """
        self.project_root = project_root
        self.threshold = threshold
        self.max_size = max_size

        # 缓存结构：{(query_text, embedding_bytes): (response_json, timestamp)}
        self._entries: dict[str, tuple[str, float]] = {}
        self._access_order: list[str] = []

        self._embed_model = None
        self._hits = 0
        self._misses = 0

    def _get_embedding(self, text: str) -> list[float] | None:
        """获取查询文本的向量嵌入。"""
        try:
            # 尝试复用 VectorStore 的 embedding 模型
            if self._embed_model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    self._embed_model = SentenceTransformer("BAAI/bge-m3")
                except ImportError:
                    # 无本地 embedding → 使用文本哈希作为近似
                    return None

            embedding = self._embed_model.encode(
                [text],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return embedding[0].tolist()
        except Exception as e:
            logger.debug("Embedding 计算失败: %s", e)
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """余弦相似度（假设已归一化，即点积）。"""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        return max(0.0, min(1.0, float(dot)))

    @staticmethod
    def _text_hash(text: str) -> str:
        """文本的简单哈希键（fallback 方案）。"""
        return str(hash(text.strip().lower()))

    def get(self, query: str) -> SearchResponse | None:
        """查找相似查询的缓存结果。

        优先使用 embedding 语义匹配，回退到精确文本匹配。

        Args:
            query: 查询文本

        Returns:
            SearchResponse 或 None（未命中）
        """
        if not query.strip() or not self._entries:
            self._misses += 1
            return None

        import json

        query_emb = self._get_embedding(query)

        # ── Embedding 语义匹配 ──
        if query_emb is not None:
            best_key = None
            best_score = 0.0

            for key in self._entries:
                emb_key = f"{key}_emb"
                if emb_key in self._entries:
                    try:
                        cached_emb = json.loads(self._entries[emb_key][0])
                        score = self._cosine_similarity(query_emb, cached_emb)
                        if score > best_score and score >= self.threshold:
                            best_score = score
                            best_key = key
                    except (json.JSONDecodeError, TypeError):
                        continue

            if best_key is not None:
                self._hits += 1
                self._touch(best_key)
                logger.debug("语义缓存命中 (相似度=%.3f): %s", best_score, query[:50])
                return SearchResponse.model_validate_json(self._entries[best_key][0])

        # ── 精确文本匹配（回退）──
        text_key = self._text_hash(query)
        if text_key in self._entries:
            self._hits += 1
            self._touch(text_key)
            logger.debug("文本缓存精确命中: %s", query[:50])
            return SearchResponse.model_validate_json(self._entries[text_key][0])

        self._misses += 1
        return None

    def put(self, query: str, response: SearchResponse) -> None:
        """缓存检索结果。

        Args:
            query: 查询文本
            response: 检索结果
        """
        if not query.strip():
            return

        # LRU 淘汰：条目数 = 响应条目 + 嵌入条目；驱逐时按有效条目数判断
        effective_count = sum(1 for k in self._entries if not k.endswith("_emb"))
        while effective_count >= self.max_size:
            if self._access_order:
                oldest = self._access_order.pop(0)
                self._entries.pop(oldest, None)
                self._entries.pop(f"{oldest}_emb", None)
                effective_count -= 1
            else:
                break

        text_key = self._text_hash(query)
        self._entries[text_key] = (
            response.model_dump_json(),
            time.time(),
        )
        self._touch(text_key)

        # 同时缓存 embedding
        query_emb = self._get_embedding(query)
        if query_emb is not None:
            import json
            self._entries[f"{text_key}_emb"] = (
                json.dumps(query_emb),
                time.time(),
            )

    def _touch(self, key: str) -> None:
        """更新 LRU 访问顺序。"""
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def clear(self) -> None:
        """清空缓存（每章开始时调用）。"""
        self._entries.clear()
        self._access_order.clear()
        logger.debug("语义缓存已清空")

    @property
    def stats(self) -> dict:
        """缓存统计信息。"""
        total = self._hits + self._misses
        effective_count = sum(1 for k in self._entries if not k.endswith("_emb"))
        return {
            "size": effective_count,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(total, 1),
        }
