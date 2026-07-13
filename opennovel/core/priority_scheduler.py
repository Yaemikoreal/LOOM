"""弹性资源调度器 — 基于章节类型和叙事张力的差异化资源分配。

根据章节类型（CLIMAX/ROUTINE/TRANSITION）自动调整 Token 预算、模型优先级、
Critic 投票模式等资源分配参数。Director 检测到张力上升时自动提升资源等级。

详见 docs/adr/0011-cost-efficiency-system.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from opennovel.core.chapter_utils import ChapterType

logger = logging.getLogger(__name__)


class ResourceTier(str, Enum):
    """资源档位，决定分配给章节的计算资源级别。"""

    PREMIUM = "premium"     # CLIMAX 章节：×2.0 预算，主力模型，3 模型投票
    STANDARD = "standard"   # ROUTINE 章节：×1.0 预算，默认模型，单模型
    ECONOMY = "economy"     # TRANSITION 章节：×0.6 预算，轻量模型，单模型


@dataclass
class ResourceBudget:
    """单章的资源预算配置。"""

    tier: ResourceTier = ResourceTier.STANDARD
    token_multiplier: float = 1.0
    use_premium_model: bool = False
    use_multi_critic: bool = False
    force_director: bool = False
    skip_director: bool = False
    max_retries: int = 5
    reasoning: str = ""


# 默认配置：各章节类型的资源倍率
DEFAULT_TIER_CONFIG: dict[ChapterType, ResourceBudget] = {
    ChapterType.CLIMAX: ResourceBudget(
        tier=ResourceTier.PREMIUM,
        token_multiplier=2.0,
        use_premium_model=True,
        use_multi_critic=True,
        force_director=True,
        skip_director=False,
        max_retries=5,
        reasoning="高潮章节：×2.0 预算，主力模型，多 Critic 投票，强制 Director",
    ),
    ChapterType.ROUTINE: ResourceBudget(
        tier=ResourceTier.STANDARD,
        token_multiplier=1.0,
        use_premium_model=False,
        use_multi_critic=False,
        force_director=False,
        skip_director=False,
        max_retries=5,
        reasoning="普通章节：×1.0 预算，默认模型，单 Critic",
    ),
    ChapterType.TRANSITION: ResourceBudget(
        tier=ResourceTier.ECONOMY,
        token_multiplier=0.6,
        use_premium_model=False,
        use_multi_critic=False,
        force_director=False,
        skip_director=True,
        max_retries=3,
        reasoning="过渡章节：×0.6 预算，轻量模型，跳过 Director，最多 3 次重试",
    ),
}

# 叙事张力阈值（Director 分析中的评分趋势，用于动态升级）
TENSION_UPGRADE_THRESHOLD = 75  # 近 3 章平均分 > 75 且上升趋势 → 升级
TENSION_DOWNGRADE_THRESHOLD = 55  # 近 3 章平均分 < 55 → 降级（保护措施）


class PriorityScheduler:
    """弹性资源调度器。

    基于章节类型分配差异化资源，支持 Director 张力信号驱动的动态升级/降级。

    使用方式:
        scheduler = PriorityScheduler()
        budget = scheduler.allocate(ChapterType.ROUTINE, tension_trend=0.2)
        budget = scheduler.allocate(ChapterType.CLIMAX)
    """

    def __init__(
        self,
        tier_config: dict[ChapterType, ResourceBudget] | None = None,
    ) -> None:
        """初始化调度器。

        Args:
            tier_config: 自定义档位配置（覆盖默认值）
        """
        self.config = {**DEFAULT_TIER_CONFIG, **(tier_config or {})}

    def allocate(
        self,
        chapter_type: ChapterType,
        tension_trend: float = 0.0,
        recent_avg_score: float | None = None,
    ) -> ResourceBudget:
        """为章节分配资源预算。

        基础分配基于章节类型，随后根据叙事张力趋势动态调整：
        - 上升趋势 + 高分 → TRANSITION/ROUTINE 可能升级
        - 下降趋势 + 低分 → CLIMAX 保持不变（不能降级高潮章节）

        Args:
            chapter_type: 章节类型（CLIMAX/ROUTINE/TRANSITION）
            tension_trend: Director 报告的张力趋势（正=上升，负=下降）
            recent_avg_score: 近几章的平均 Critic 评分（用于升级判断）

        Returns:
            ResourceBudget 资源预算配置
        """
        budget = self.config.get(chapter_type, self.config[ChapterType.ROUTINE])

        # 深拷贝以避免修改默认配置
        budget = ResourceBudget(
            tier=budget.tier,
            token_multiplier=budget.token_multiplier,
            use_premium_model=budget.use_premium_model,
            use_multi_critic=budget.use_multi_critic,
            force_director=budget.force_director,
            skip_director=budget.skip_director,
            max_retries=budget.max_retries,
            reasoning=budget.reasoning,
        )

        # ── 动态升级：张力上升 → 提升资源 ──
        if (
            budget.tier != ResourceTier.PREMIUM
            and tension_trend > 0.1
            and (recent_avg_score is None or recent_avg_score >= TENSION_UPGRADE_THRESHOLD)
        ):
            if budget.tier == ResourceTier.ECONOMY:
                # TRANSITION → ROUTINE
                budget.tier = ResourceTier.STANDARD
                budget.token_multiplier = 1.0
                budget.use_premium_model = False
                budget.skip_director = False
                budget.reasoning += (
                    f" | 动态升级：张力趋势 +{tension_trend:.2f}，"
                    f"近章均分 {recent_avg_score}，ECONOMY → STANDARD"
                )
            elif budget.tier == ResourceTier.STANDARD:
                # ROUTINE → PREMIUM
                budget.tier = ResourceTier.PREMIUM
                budget.token_multiplier = 2.0
                budget.use_premium_model = True
                budget.reasoning += (
                    f" | 动态升级：张力趋势 +{tension_trend:.2f}，"
                    f"近章均分 {recent_avg_score}，STANDARD → PREMIUM"
                )

        # ── 动态降级：张力下降 + 低分 ──
        if (
            budget.tier != ResourceTier.PREMIUM
            and tension_trend < -0.1
            and recent_avg_score is not None
            and recent_avg_score < TENSION_DOWNGRADE_THRESHOLD
        ):
            if budget.tier == ResourceTier.STANDARD and chapter_type != ChapterType.CLIMAX:
                budget.tier = ResourceTier.ECONOMY
                budget.token_multiplier = 0.6
                budget.max_retries = 3
                budget.reasoning += (
                    f" | 动态降级：张力趋势 {tension_trend:.2f}，"
                    f"近章均分 {recent_avg_score}，STANDARD → ECONOMY"
                )

        logger.debug(
            "资源分配: %s → %s (倍率 ×%.1f, premium=%s, multi_critic=%s)",
            chapter_type.value,
            budget.tier.value,
            budget.token_multiplier,
            budget.use_premium_model,
            budget.use_multi_critic,
        )
        return budget

    def adjust_token_budget(
        self,
        base_budget: int,
        budget_config: ResourceBudget,
    ) -> int:
        """根据资源预算倍率调整 Token 限制。

        Args:
            base_budget: 基础 Token 预算（如 STANDARD_TOKEN_BUDGET = 48000）
            budget_config: 资源预算配置

        Returns:
            调整后的 Token 预算
        """
        return int(base_budget * budget_config.token_multiplier)

    def get_model_tier(self, budget_config: ResourceBudget) -> str:
        """获取应使用的模型档位标识。

        Args:
            budget_config: 资源预算配置

        Returns:
            "premium" / "default" / "economy"
        """
        if budget_config.use_premium_model:
            return "premium"
        elif budget_config.tier == ResourceTier.ECONOMY:
            return "economy"
        else:
            return "default"
