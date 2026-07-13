"""注意力预算管理器 — 为上下文片段打标并分配 Token 配额。

为每个上下文片段打上重要性和时效性标签，按优先级分配 Token 配额。
高优先级内容置于上下文开头和结尾（LLM 注意力最强的位置），
低优先级内容放在中间。

详见 docs/adr/0008-dynamic-context-engineering.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Importance(str, Enum):
    """上下文片段的重要性等级。"""

    CRITICAL = "critical"    # 核心设定，不可丢失
    HIGH = "high"            # 当前章节关键信息
    MEDIUM = "medium"        # 辅助信息
    LOW = "low"              # 可选参考


class Freshness(str, Enum):
    """上下文片段的时效性等级。"""

    PERMANENT = "permanent"  # 永久有效（如 CANON 核心规则）
    CURRENT = "current"      # 当前章节有效
    RECENT = "recent"        # 近几章有效
    STALE = "stale"          # 可能已过时


@dataclass
class ContextFragment:
    """带优先级标签的上下文片段。"""

    content: str
    """文本内容"""

    source: str
    """来源标识（如 "canon/magic", "state/char_001"）"""

    importance: Importance = Importance.MEDIUM
    """重要性等级"""

    freshness: Freshness = Freshness.CURRENT
    """时效性等级"""

    token_estimate: int = 0
    """估算的 Token 数"""

    priority_score: float = 0.0
    """综合优先级分数（自动计算）"""

    def __post_init__(self) -> None:
        if self.priority_score == 0.0:
            self.priority_score = self._compute_priority()

    def _compute_priority(self) -> float:
        """根据重要性和时效性计算综合优先级分数。

        优先级分数 = 重要性权重 × 时效性权重
        """
        importance_weights = {
            Importance.CRITICAL: 1.0,
            Importance.HIGH: 0.8,
            Importance.MEDIUM: 0.5,
            Importance.LOW: 0.2,
        }
        freshness_weights = {
            Freshness.PERMANENT: 1.0,
            Freshness.CURRENT: 0.9,
            Freshness.RECENT: 0.6,
            Freshness.STALE: 0.3,
        }
        imp_w = importance_weights.get(self.importance, 0.5)
        fresh_w = freshness_weights.get(self.freshness, 0.5)
        return imp_w * fresh_w


# 注意力预算分配比例（按优先级分数从高到低分配 Token）
DEFAULT_BUDGET_RATIOS: dict[str, float] = {
    "head": 0.35,    # 上下文开头：35%（最重要内容）
    "body": 0.40,    # 上下文中间：40%（中等内容）
    "tail": 0.25,    # 上下文结尾：25%（次重要内容）
}


class AttentionBudgetManager:
    """注意力预算管理器。

    管理上下文片段的优先级标签和 Token 配额分配。
    将高优先级片段置于 LLM 注意力最强的位置（开头和结尾）。

    使用方式:
        manager = AttentionBudgetManager(total_budget=48000)
        manager.add_fragment(fragment)
        ordered_context = manager.assemble()
    """

    def __init__(
        self,
        total_budget: int = 48000,
        budget_ratios: dict[str, float] | None = None,
    ) -> None:
        """初始化预算管理器。

        Args:
            total_budget: 总 Token 预算
            budget_ratios: 三段预算比例配置
        """
        self.total_budget = total_budget
        self.ratios = budget_ratios or DEFAULT_BUDGET_RATIOS
        self._fragments: list[ContextFragment] = []

    def add_fragment(self, fragment: ContextFragment) -> None:
        """添加上下文片段。

        Args:
            fragment: 上下文片段
        """
        self._fragments.append(fragment)

    def add_fragments(self, fragments: list[ContextFragment]) -> None:
        """批量添加上下文片段。"""
        self._fragments.extend(fragments)

    @property
    def fragment_count(self) -> int:
        return len(self._fragments)

    def get_head_budget(self) -> int:
        """获取开头段的 Token 预算。"""
        return int(self.total_budget * self.ratios.get("head", 0.35))

    def get_body_budget(self) -> int:
        """获取中间段的 Token 预算。"""
        return int(self.total_budget * self.ratios.get("body", 0.40))

    def get_tail_budget(self) -> int:
        """获取结尾段的 Token 预算。"""
        return int(self.total_budget * self.ratios.get("tail", 0.25))

    def assemble(self) -> list[ContextFragment]:
        """按注意力优先级组装上下文片段顺序。

        组装策略：
        1. 按优先级分数降序排列所有片段
        2. 最高优先级片段 → 开头（head，占 35% 预算）
        3. 中等优先级片段 → 中间（body，占 40% 预算）
        4. 次高优先级片段 → 结尾（tail，占 25% 预算）

        Returns:
            排序后的 ContextFragment 列表
        """
        if not self._fragments:
            return []

        # 按优先级降序排列
        sorted_fragments = sorted(
            self._fragments,
            key=lambda f: f.priority_score,
            reverse=True,
        )

        head_budget = self.get_head_budget()
        body_budget = self.get_body_budget()
        tail_budget = self.get_tail_budget()

        head: list[ContextFragment] = []
        body: list[ContextFragment] = []
        tail: list[ContextFragment] = []

        head_tokens = 0
        body_tokens = 0
        tail_tokens = 0

        for frag in sorted_fragments:
            tokens = frag.token_estimate or self._estimate_tokens(frag.content)

            if head_tokens + tokens <= head_budget:
                head.append(frag)
                head_tokens += tokens
            elif tail_tokens + tokens <= tail_budget:
                # 次高优先级内容放尾部（LLM 对结尾注意力也较高）
                tail.append(frag)
                tail_tokens += tokens
            elif body_tokens + tokens <= body_budget:
                body.append(frag)
                body_tokens += tokens
            else:
                # 超预算，截断（保留片段开头）
                available = self.total_budget - head_tokens - body_tokens - tail_tokens
                if available > 50:
                    truncated = ContextFragment(
                        content=self._truncate(frag.content, available),
                        source=frag.source + " (truncated)",
                        importance=frag.importance,
                        freshness=frag.freshness,
                    )
                    body.append(truncated)
                break

        # 组装顺序：head → body → tail
        result = head + body + list(reversed(tail))  # tail 逆序使最高优先级的在最后

        logger.debug(
            "注意力预算组装: %d 片段 → head=%d body=%d tail=%d (预算 %d)",
            len(self._fragments),
            len(head),
            len(body),
            len(tail),
            self.total_budget,
        )
        return result

    def assemble_text(self) -> list[str]:
        """组装为纯文本列表（供 ContextAssembler 注入）。

        Returns:
            排序后的文本片段列表
        """
        return [f.content for f in self.assemble()]

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """快速估算 Token 数。"""
        cn_chars = sum(1 for c in text if "一" <= c <= "鿿")
        en_chars = len(text) - cn_chars
        return int(cn_chars / 1.67 + en_chars / 4.0)

    @staticmethod
    def _truncate(text: str, max_tokens: int) -> str:
        """截断文本到最大 Token 数以内。"""
        if max_tokens <= 0:
            return ""
        # 保守估算：1 token ≈ 3 字符
        max_chars = max_tokens * 3
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "..."

    def clear(self) -> None:
        """清空所有片段。"""
        self._fragments.clear()
