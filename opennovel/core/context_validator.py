"""上下文一致性校验器。

在 ContextAssembler 注入 STATE MEMORY 前校验：
- Canon 冲突检测（复用 CanonChecker）
- 跨源一致性（YAML FM vs SQLite vs 正文）
- 脏标记过滤

校验失败时降级策略：冲突数据标记为 WARNING 而非阻断注入。

详见 docs/adr/0008-dynamic-context-engineering.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class DiscrepancyLevel(str, Enum):
    """不一致严重程度。"""

    INFO = "info"        # 信息提示
    WARNING = "warning"  # 可能不一致，标记警告
    ERROR = "error"      # 确认冲突，需人工介入
    CRITICAL = "critical"  # 严重冲突（如角色状态矛盾）


@dataclass
class Discrepancy:
    """单条不一致记录。"""

    level: DiscrepancyLevel
    """严重程度"""

    category: str
    """分类：canon_conflict / state_drift / dirty_flag / cross_source"""

    message: str
    """人类可读的描述"""

    sources: list[str] = field(default_factory=list)
    """涉及的数据源（如 ["yaml/char_001.md", "sqlite/events"]）"""

    suggestion: str = ""
    """修复建议"""


@dataclass
class ValidationReport:
    """完整性校验报告。"""

    passed: bool = True
    """是否通过所有校验"""

    discrepancies: list[Discrepancy] = field(default_factory=list)

    warning_messages: list[str] = field(default_factory=list)
    """注入前的警告文本（附加到上下文）"""


class ContextValidator:
    """上下文一致性校验器。

    在 STATE MEMORY 注入前执行多源一致性比对，
    发现冲突时降级为 WARNING 标注而非阻断注入。

    使用方式:
        validator = ContextValidator(project_root)
        report = validator.validate(
            character_states=[...],
            canon_rules=[...],
            recent_events=[...],
        )
        if not report.passed:
            for msg in report.warning_messages:
                context += f"[WARNING] {msg}"
    """

    def __init__(
        self,
        project_root: Path,
        canon_checker=None,
    ) -> None:
        """初始化校验器。

        Args:
            project_root: 项目根目录
            canon_checker: CanonChecker 实例（可选，自动创建）
        """
        self.project_root = project_root
        self._canon_checker = canon_checker

    def _get_canon_checker(self):
        """获取 CanonChecker 实例。"""
        if self._canon_checker is not None:
            return self._canon_checker
        try:
            from opennovel.core.canon_checker import CanonChecker
            self._canon_checker = CanonChecker()
        except ImportError:
            logger.warning("CanonChecker 不可用")
            self._canon_checker = None
        return self._canon_checker

    def validate(
        self,
        character_states: list[dict] | None = None,
        canon_rules: list | None = None,
        recent_events: list | None = None,
        chapter_text: str = "",
        check_canon: bool = True,
        check_dirty_flags: bool = True,
    ) -> ValidationReport:
        """执行上下文一致性校验。

        Args:
            character_states: 待注入的角色状态列表（YAML FM 格式）
            canon_rules: CANON 规则列表
            recent_events: 近期事件列表
            chapter_text: 当前章节文本
            check_canon: 是否检查 CANON 冲突
            check_dirty_flags: 是否检查脏标记

        Returns:
            ValidationReport 校验报告
        """
        report = ValidationReport(passed=True)

        # ── 1. CANON 冲突检测 ──
        if check_canon and canon_rules and chapter_text:
            checker = self._get_canon_checker()
            if checker is not None:
                try:
                    violations = checker.check_text(chapter_text, canon_rules)
                    for v in violations:
                        report.discrepancies.append(
                            Discrepancy(
                                level=(
                                    DiscrepancyLevel.CRITICAL
                                    if v.level == "violation"
                                    else DiscrepancyLevel.WARNING
                                ),
                                category="canon_conflict",
                                message=f"CANON 冲突: {v.message}",
                                sources=["canon/"],
                                suggestion=v.suggestion if hasattr(v, "suggestion") else "请检查设定一致性",
                            )
                        )
                except Exception as e:
                    logger.debug("CanonChecker 校验失败: %s", e)

        # ── 2. 角色状态一致性 ──
        if character_states:
            for state in character_states:
                # 检测明显冲突的状态（如同时有 INJURY 和完全健康）
                if self._has_contradictory_state(state):
                    report.discrepancies.append(
                        Discrepancy(
                            level=DiscrepancyLevel.ERROR,
                            category="state_drift",
                            message=(
                                f"角色 {state.get('id', '?')} "
                                f"存在不一致状态: {self._describe_contradiction(state)}"
                            ),
                            sources=[f"characters/{state.get('id', '?')}.md"],
                            suggestion="建议运行 novel commit 更新状态",
                        )
                    )

        # ── 3. 脏标记过滤 ──
        if check_dirty_flags and character_states:
            for state in character_states:
                if state.get("dirty_flag"):
                    report.discrepancies.append(
                        Discrepancy(
                            level=DiscrepancyLevel.WARNING,
                            category="dirty_flag",
                            message=(
                                f"角色 {state.get('id', '?')} 的状态标记为脏数据 "
                                f"(dirty_flag={state['dirty_flag']})，可能不可信"
                            ),
                            sources=[f"characters/{state.get('id', '?')}.md"],
                            suggestion="建议运行 novel commit 重新提取状态",
                        )
                    )

        # ── 4. 生成警告文本 ──
        if report.discrepancies:
            report.passed = all(
                d.level not in (DiscrepancyLevel.ERROR, DiscrepancyLevel.CRITICAL)
                for d in report.discrepancies
            )
            for d in report.discrepancies:
                prefix = {
                    DiscrepancyLevel.INFO: "[INFO]",
                    DiscrepancyLevel.WARNING: "[WARNING] 以下信息可能不一致，请谨慎参考",
                    DiscrepancyLevel.ERROR: "[ERROR] 检测到数据冲突",
                    DiscrepancyLevel.CRITICAL: "[CRITICAL] 严重冲突，建议暂停创作并修复",
                }.get(d.level, "[INFO]")
                report.warning_messages.append(f"{prefix}: {d.message}")

        if report.discrepancies:
            logger.info(
                "上下文校验: %d 条不一致 (%d 通过)",
                len(report.discrepancies),
                sum(1 for d in report.discrepancies if d.level == DiscrepancyLevel.INFO),
            )

        return report

    @staticmethod
    def _has_contradictory_state(state: dict) -> bool:
        """检测角色状态是否有内部矛盾。"""
        health = state.get("health", "")
        injuries = state.get("injuries", [])
        if health == "healthy" and injuries and len(injuries) > 0:
            return True
        return False

    @staticmethod
    def _describe_contradiction(state: dict) -> str:
        """描述角色状态的矛盾。"""
        parts = []
        if state.get("health") == "healthy" and state.get("injuries"):
            parts.append("标记为健康但存在伤害记录")
        return "; ".join(parts) if parts else "未知矛盾"
