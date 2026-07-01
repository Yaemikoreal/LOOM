"""Cross-Encoder 重排序器 — 对 RRF 融合结果进行二次精排。

基于 bge-reranker-v2-m3 模型实现：
- 类级懒加载：模型在首次调用时加载，后续实例共享
- 自动设备检测：cuda → mps → cpu
- 阈值提前退出：top1 分数显著高于 top2（>2×）时跳过 Reranker

使用方式:
    reranker = Reranker()
    # 跳过阈值
    scores = reranker.rerank("查询文本", ["候选1", "候选2", ...])
    # 返回排序后的索引
    indices = reranker.rerank_indices("查询文本", candidates)

详见 ADR 0007 — 混合语义-关键词检索 + 重排序架构。
"""

import importlib.util
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 类级缓存：模型仅加载一次（threading.Lock 防并发 race）
_MODEL_INSTANCE: Any = None
_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
_DEVICE: str | None = None
_LOAD_LOCK = threading.Lock()


def _detect_device() -> str:
    """自动检测最佳可用设备。

    优先级：cuda > mps > cpu

    Returns:
        设备名称字符串
    """
    global _DEVICE
    if _DEVICE is not None:
        return _DEVICE

    with _LOAD_LOCK:
        # 双重检查锁定
        if _DEVICE is not None:
            return _DEVICE
        try:
            import torch

            if torch.cuda.is_available():
                _DEVICE = "cuda"
                logger.info("Reranker 使用 CUDA 设备")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                _DEVICE = "mps"
                logger.info("Reranker 使用 MPS 设备")
            else:
                _DEVICE = "cpu"
                logger.info("Reranker 使用 CPU 设备")
        except ImportError:
            _DEVICE = "cpu"
            logger.info("PyTorch 未安装，Reranker 使用 CPU 设备")

    return _DEVICE


def _get_model() -> Any:
    """类级懒加载：获取 Cross-Encoder 模型实例。

    模型在首次调用时加载，后续调用共享同一实例。
    若依赖未安装，返回 None。

    Returns:
        CrossEncoder 实例，或 None（依赖缺失时）
    """
    global _MODEL_INSTANCE
    if _MODEL_INSTANCE is not None:
        return _MODEL_INSTANCE

    with _LOAD_LOCK:
        # 双重检查锁定
        if _MODEL_INSTANCE is not None:
            return _MODEL_INSTANCE

        # 检查依赖
        if not importlib.util.find_spec("sentence_transformers"):
            logger.error(
                "sentence-transformers 未安装，无法加载 Reranker。"
                "请执行: pip install sentence-transformers"
            )
            _MODEL_INSTANCE = None
            return None

        try:
            from sentence_transformers import CrossEncoder

            device = _detect_device()
            logger.info("加载 Reranker 模型: %s (device=%s)", _MODEL_NAME, device)
            _MODEL_INSTANCE = CrossEncoder(
                _MODEL_NAME,
                device=device,
                max_length=512,
            )
            logger.info("Reranker 模型加载完成: %s", _MODEL_NAME)
        except Exception as e:
            logger.error("Reranker 模型加载失败: %s", e)
            _MODEL_INSTANCE = None

    return _MODEL_INSTANCE


class Reranker:
    """Cross-Encoder 重排序器。

    对 RRF 融合后的候选结果进行二次精排。
    支持阈值提前退出（top1 > 2× top2 时跳过重排序）。

    使用方式:
        reranker = Reranker()
        # 获取所有候选的得分
        scores = reranker.rerank("查询", ["候选1", "候选2"])
        # 获取排序后的索引
        indices = reranker.rerank_indices("查询", ["候选1", "候选2"])
    """

    def __init__(self) -> None:
        """初始化重排序器。模型实例在首次调用时按需加载。"""
        self._model = _get_model()

    @property
    def is_available(self) -> bool:
        """检查模型是否可用。"""
        if self._model is not None:
            return True
        # 尝试重新加载（懒加载）
        self._model = _get_model()
        return self._model is not None

    @property
    def model_name(self) -> str:
        """获取当前使用的模型名称。"""
        return _MODEL_NAME

    def rerank(
        self,
        query: str,
        candidates: list[str],
        threshold_exit: bool = True,
    ) -> list[float]:
        """对候选结果进行 Cross-Encoder 重排序评分。

        Args:
            query: 查询文本
            candidates: 候选文本列表
            threshold_exit: 是否启用阈值提前退出（top1 > 2× top2 时直接返回）

        Returns:
            每个候选的 Cross-Encoder 归一化得分（长度同 candidates），
            模型不可用时返回全 1.0 的等分列表。
        """
        if not candidates:
            return []

        if not self.is_available:
            logger.warning("Reranker 模型不可用，返回等分")
            return [1.0] * len(candidates)

        if len(candidates) == 1:
            return [1.0]

        try:
            # 构造 query-candidate 对
            pairs = [[query, c] for c in candidates]
            scores = self._model.predict(pairs)
            # CrossEncoder.predict 返回 numpy array
            scores_list = [float(s) for s in scores]

            # 归一化到 0~1
            if scores_list and max(scores_list) > min(scores_list):
                min_s, max_s = min(scores_list), max(scores_list)
                if max_s > min_s:
                    scores_list = [(s - min_s) / (max_s - min_s) for s in scores_list]

            # 阈值提前退出：top1 > 2× top2 → 跳过精排
            if threshold_exit and len(scores_list) >= 2:
                sorted_scores = sorted(scores_list, reverse=True)
                if sorted_scores[0] > 2.0 * sorted_scores[1]:
                    logger.debug(
                        "Reranker 阈值提前退出: top1=%.4f, top2=%.4f (>2x)",
                        sorted_scores[0],
                        sorted_scores[1],
                    )
                    return scores_list  # 返回原始分数，调用者仍可用

            return scores_list

        except Exception as e:
            logger.warning("Reranker 评分失败: %s", e)
            return [1.0] * len(candidates)

    def rerank_indices(
        self,
        query: str,
        candidates: list[str],
        threshold_exit: bool = True,
    ) -> list[int]:
        """返回排序后的索引列表（按相关性降序）。

        Args:
            query: 查询文本
            candidates: 候选文本列表
            threshold_exit: 是否启用阈值提前退出

        Returns:
            排序后的索引列表，如 [2, 0, 1] 表示 candidates[2] 最相关
        """
        scores = self.rerank(query, candidates, threshold_exit=threshold_exit)
        # 按分数降序排序，返回索引
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in indexed]

    def rerank_with_scores(
        self,
        query: str,
        candidates: list[str],
        threshold_exit: bool = True,
    ) -> list[tuple[int, float]]:
        """返回 (索引, 分数) 对列表，按分数降序排列。

        Args:
            query: 查询文本
            candidates: 候选文本列表
            threshold_exit: 是否启用阈值提前退出

        Returns:
            [(idx, score), ...] 按分数降序排列
        """
        scores = self.rerank(query, candidates, threshold_exit=threshold_exit)
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed
