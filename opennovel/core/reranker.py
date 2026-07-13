"""Cross-Encoder 重排序器。

在 RRF 融合粗排后，对 (query, candidate) 对进行深度语义匹配精排。
使用 BAAI/bge-reranker-v2-m3 模型，类级懒加载，自动设备检测。

优化策略：
- 阈值提前退出：RRF top1 分数 > top2 ×2 时跳过 Reranker
- 最大处理 50 个候选对

详见 docs/adr/0007-hybrid-search-and-reranking-architecture.md。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opennovel.schemas.search import SearchResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Cross-Encoder 模型名称
_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# 单次重排序的最大候选数
_MAX_CANDIDATES = 50

# 返回的最大精排结果数
_MAX_TOP = 5

# RRF top1 相对于 top2 的倍数阈值（快速路径），超过此值跳过重排序
# 较高值=更保守（更少跳过），较低值=更激进（更多跳过）
_EARLY_EXIT_FAST_RATIO = 3.0


class Reranker:
    """Cross-Encoder 重排序器。

    使用 BAAI/bge-reranker-v2-m3 对候选文本进行精排。
    类级懒加载模型，首次调用 compute_score() 或 rerank() 时加载。

    无 GPU 时可设置 reranker_enabled=False 降级为纯 RRF 模式。

    使用方式:
        reranker = Reranker()
        reranked = reranker.rerank(query, candidates)
        # candidates 在 rerank_score 修改后原地返回
    """

    _model = None
    _model_available: bool | None = None  # None = 未检测, True = 可用, False = 不可用

    def __init__(self, enabled: bool = True) -> None:
        """初始化重排序器。

        Args:
            enabled: 是否启用重排序（可动态关闭以节省资源）
        """
        self.enabled = enabled

    @classmethod
    def _check_availability(cls) -> bool:
        """检测 sentence-transformers 和模型是否可用（类级缓存）。

        Returns:
            True 表示可用
        """
        if cls._model_available is not None:
            return cls._model_available

        try:
            from sentence_transformers import CrossEncoder  # noqa: F401
            cls._model_available = True
        except ImportError:
            logger.warning(
                "sentence-transformers 未安装，Cross-Encoder 重排序不可用。"
                "请执行: pip install sentence-transformers"
            )
            cls._model_available = False

        return cls._model_available

    @classmethod
    def get_model(cls):
        """获取 Cross-Encoder 模型实例（类级懒加载 + 缓存）。

        Returns:
            CrossEncoder 模型实例，不可用时返回 None
        """
        if cls._model is not None:
            return cls._model

        if not cls._check_availability():
            return None

        try:
            from sentence_transformers import CrossEncoder

            cls._model = CrossEncoder(
                _RERANKER_MODEL,
                trust_remote_code=True,
            )
            logger.info("Cross-Encoder 模型加载完成: %s", _RERANKER_MODEL)
        except Exception as e:
            logger.warning("Cross-Encoder 模型加载失败: %s", e)
            cls._model_available = False
            cls._model = None

        return cls._model

    def should_skip_rerank(self, candidates: list[SearchResult]) -> bool:
        """检测是否应跳过重排序（阈值提前退出 + 动态置信度）。

        两阶段决策（ADR 0009 动态置信度重排）：
        1. 快速路径：top1 > top2 × 3 → 压倒性优势，直接跳过
        2. 置信度路径：Top 5 分数变异系数 CV ≤ 0.3 → 分数集中，跳过
        3. 否则 → 不跳过（需要 Cross-Encoder 精排）

        Args:
            candidates: RRF 融合后的候选结果列表

        Returns:
            True 表示应跳过重排序
        """
        if len(candidates) < 2:
            return True

        top1_score = candidates[0].score
        top2_score = candidates[1].score if len(candidates) > 1 else 0.0

        # ── 快速路径：top1 压倒性优势 ──
        if top1_score > 0.0 and (top1_score / max(top2_score, 0.001)) >= _EARLY_EXIT_FAST_RATIO:
            logger.debug("RRF top1 压倒性优势（%.3f vs %.3f），跳过重排序", top1_score, top2_score)
            return True

        # ── 置信度路径：基于 Top 5 分数的变异系数 ──
        scores = [c.score for c in candidates[:5] if c.score > 0]
        if len(scores) < 2:
            return True

        mean_score = sum(scores) / len(scores)
        if mean_score <= 0.0:
            return False

        # 变异系数 CV = σ / μ
        variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
        std_dev = variance ** 0.5
        cv = std_dev / mean_score

        # CV ≤ 0.3 → 分数集中，top1 明显优于其他
        if cv <= 0.3:
            logger.debug("RRF 分数集中（CV=%.3f），跳过重排序", cv)
            return True

        logger.debug("RRF 分数分散（CV=%.3f），需要 Cross-Encoder 精排", cv)
        return False

    def rerank(
        self,
        query: str,
        candidates: list[SearchResult],
    ) -> list[SearchResult]:
        """对候选结果进行 Cross-Encoder 重排序。

        原地修改 candidates 的 rerank_score 字段，按精排分数降序排列后返回。

        不可用或禁用时，仅保留 top_k 结果（保持原序）。

        Args:
            query: 原始查询文本
            candidates: RRF 融合后的候选结果（已按 score 降序）

        Returns:
            精排后的结果列表（rerank_score 降序，最多 5 条）
        """
        if not candidates:
            return candidates

        if not self.enabled:
            return candidates[:_MAX_TOP]

        model = self.get_model()
        if model is None:
            logger.debug("Reranker 不可用，返回 RRF top %d", _MAX_TOP)
            return candidates[:_MAX_TOP]

        # 阈值提前退出
        if self.should_skip_rerank(candidates):
            logger.debug("RRF top1 压倒性优势（%.3f），跳过重排序", candidates[0].score)
            return candidates[:_MAX_TOP]

        # 截断到最大候选数
        to_rerank = candidates[:_MAX_CANDIDATES]

        try:
            pairs = [(query, c.text) for c in to_rerank]
            scores: list[float] = model.predict(
                pairs,
                batch_size=16,
                show_progress_bar=False,
            )

            # 将分数写回
            for candidate, score in zip(to_rerank, scores):
                candidate.rerank_score = float(score)

            # 按精排分数降序排列
            to_rerank.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)

        except Exception as e:
            logger.warning("Cross-Encoder 重排序失败: %s，回退到 RRF 顺序", e)
            return candidates[:_MAX_TOP]

        logger.debug(
            "Cross-Encoder 重排序完成: %d 候选 → top %d 精排",
            len(to_rerank),
            min(_MAX_TOP, len(to_rerank)),
        )
        return to_rerank[:_MAX_TOP]
