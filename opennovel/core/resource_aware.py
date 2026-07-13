"""系统资源感知降级器。

检测系统资源状况（内存/CPU/GPU/电池），自动调整计算强度：
- 检索 top_k 缩减
- Cross-Encoder 跳过
- 上下文策略降级
- 向量索引精度调整

详见 docs/adr/0011-cost-efficiency-system.md。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class ResourceProfile:
    """系统资源档位。"""

    HIGH = "high"       # 资源充裕：全面跑，不降级
    MEDIUM = "medium"   # 资源正常：适度降级
    LOW = "low"         # 资源紧张：激进降级
    MINIMAL = "minimal" # 资源极低：最小化计算


class ResourceAwareDegrader:
    """系统资源感知降级器。

    自动检测 CPU、内存、GPU、电源状态，映射到资源档位，
    并根据档位调整各模块的计算强度参数。

    使用方式:
        degrader = ResourceAwareDegrader()
        profile = degrader.get_profile()
        adjusted_top_k = degrader.adjust_retrieval_top_k(15)
        if degrader.should_skip_reranker():
            ...
    """

    def __init__(self, forced_profile: str | None = None) -> None:
        """初始化降级器。

        Args:
            forced_profile: 强制指定的资源档位（"high"/"medium"/"low"/"minimal"/"auto"）。
                           为 None 或 "auto" 时自动检测。
        """
        self._forced = None if forced_profile in (None, "auto") else forced_profile
        self._cached_profile: str | None = None

    def get_profile(self) -> str:
        """获取当前系统资源档位。

        Returns:
            资源档位字符串：high / medium / low / minimal
        """
        if self._forced is not None:
            return self._forced

        if self._cached_profile is not None:
            return self._cached_profile

        profile = self._detect_profile()
        self._cached_profile = profile
        logger.debug("资源档位检测: %s", profile)
        return profile

    def _detect_profile(self) -> str:
        """自动检测系统资源状况。"""
        import platform

        score = 0

        # ── 内存检测 ──
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_percent = mem.percent
            if mem_percent < 50:
                score += 3  # 内存充裕
            elif mem_percent < 75:
                score += 2
            elif mem_percent < 90:
                score += 1
            # >90%: score += 0（内存紧张）
        except ImportError:
            # psutil 不可用时默认中等
            score += 2

        # ── CPU 检测 ──
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=0.1)
            if cpu_percent < 30:
                score += 2
            elif cpu_percent < 70:
                score += 1
            # >70%: score += 0
        except ImportError:
            # 通过 os.cpu_count 粗略判断
            cpu_count = os.cpu_count() or 1
            if cpu_count >= 8:
                score += 2
            elif cpu_count >= 4:
                score += 1

        # ── GPU 检测 ──
        has_gpu = False
        try:
            import torch
            if torch.cuda.is_available():
                has_gpu = True
                score += 2
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                has_gpu = True
                score += 1
        except ImportError:
            pass

        # ── 电池检测（笔记本节能模式）──
        try:
            import psutil
            battery = psutil.sensors_battery()
            if battery is not None and not battery.power_plugged:
                score -= 1  # 电池供电时降一档
        except ImportError:
            pass

        # ── 平台修正 ──
        system = platform.system()
        if system == "Darwin":
            # macOS 通常资源配置较好
            score += 1
        elif system == "Linux" and not has_gpu:
            # 无 GPU Linux 可能是服务器或低配设备
            pass

        # ── 分数 → 档位映射 ──
        if score >= 6:
            return ResourceProfile.HIGH
        elif score >= 4:
            return ResourceProfile.MEDIUM
        elif score >= 2:
            return ResourceProfile.LOW
        else:
            return ResourceProfile.MINIMAL

    def adjust_retrieval_top_k(self, base: int) -> int:
        """根据资源档位调整检索 top_k。

        Args:
            base: 基础 top_k 值

        Returns:
            调整后的 top_k
        """
        profile = self.get_profile()
        adjustments = {
            ResourceProfile.HIGH: 1.0,
            ResourceProfile.MEDIUM: 0.8,
            ResourceProfile.LOW: 0.5,
            ResourceProfile.MINIMAL: 0.3,
        }
        factor = adjustments.get(profile, 0.5)
        return max(3, int(base * factor))

    def should_skip_reranker(self) -> bool:
        """资源紧张时自动跳过 Cross-Encoder 重排序。

        Returns:
            True 表示应跳过 Reranker
        """
        profile = self.get_profile()
        return profile in (ResourceProfile.LOW, ResourceProfile.MINIMAL)

    def should_use_frugal_strategy(self) -> bool:
        """资源极低时强制使用 FRUGAL 上下文策略。

        Returns:
            True 表示应使用 FRUGAL 策略
        """
        profile = self.get_profile()
        return profile == ResourceProfile.MINIMAL

    def get_max_chunk_tokens(self, base: int = 512) -> int:
        """根据资源调整分块大小。

        Args:
            base: 基础分块 Token 数

        Returns:
            调整后的分块大小
        """
        profile = self.get_profile()
        if profile == ResourceProfile.MINIMAL:
            return max(256, base // 2)
        return base

    def invalidate_cache(self) -> None:
        """清除缓存的资源档位（用于手动刷新）。"""
        self._cached_profile = None
