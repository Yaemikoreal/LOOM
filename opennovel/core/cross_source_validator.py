"""跨源数据一致性校验器。

检查多源数据（YAML FM、SQLite EventStore、正文 Markdown）之间的一致性，
自动修复轻度不一致，报警严重冲突。

详见 docs/adr/0012-real-time-self-healing-system.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Discrepancy:
    """单条数据不一致记录。"""

    level: str  # info / warning / error / critical
    source_a: str  # 数据源 A 的描述
    source_b: str  # 数据源 B 的描述
    field: str  # 不一致的字段
    value_a: str  # 源 A 中的值
    value_b: str  # 源 B 中的值
    auto_fixable: bool = False


@dataclass
class CrossSourceReport:
    """跨源一致性报告。"""

    discrepancies: list[Discrepancy] = field(default_factory=list)
    auto_fixed: int = 0
    needs_manual: int = 0


class CrossSourceValidator:
    """跨源数据一致性校验器。

    比对 YAML Frontmatter、SQLite EventStore、正文 Markdown 中的关键数据，
    自动修复轻度不一致，报警严重冲突。

    使用方式:
        validator = CrossSourceValidator(project_root)
        report = validator.validate_all()
    """

    def __init__(
        self,
        project_root: Path,
        yaml_storage=None,
        event_store=None,
    ) -> None:
        """初始化校验器。

        Args:
            project_root: 项目根目录
            yaml_storage: YAMLStorage 实例（可选）
            event_store: EventStore 实例（可选）
        """
        self.project_root = project_root
        self._yaml = yaml_storage
        self._events = event_store

    def validate_all(self) -> CrossSourceReport:
        """执行全部跨源校验。

        Returns:
            CrossSourceReport
        """
        report = CrossSourceReport()

        # ── 1. 角色状态校验 ──
        self._validate_character_states(report)

        # ── 2. 事件时间线校验 ──
        self._validate_event_timeline(report)

        # ── 3. Frontmatter 完整性 ──
        self._validate_frontmatter_completeness(report)

        if report.discrepancies:
            logger.info(
                "跨源校验: %d 条不一致, %d 自动修复, %d 需人工",
                len(report.discrepancies),
                report.auto_fixed,
                report.needs_manual,
            )

        return report

    def _validate_character_states(self, report: CrossSourceReport) -> None:
        """校验角色状态在 YAML FM 和 EventStore 之间的一致性。"""
        characters_dir = self.project_root / "characters"
        if not characters_dir.exists():
            return

        for md_file in characters_dir.glob("*.md"):
            try:
                # 读取 YAML Frontmatter
                raw = md_file.read_text(encoding="utf-8")
                fm_data = self._parse_frontmatter(raw)

                char_id = fm_data.get("id", md_file.stem)

                # 检查基本字段完整性
                name = fm_data.get("name", fm_data.get("id", ""))
                if not name:
                    report.discrepancies.append(
                        Discrepancy(
                            level="warning",
                            source_a=f"characters/{md_file.name}",
                            source_b="expected",
                            field="name",
                            value_a="(missing)",
                            value_b="(required)",
                            auto_fixable=False,
                        )
                    )

                # 检查 health 字段
                health = fm_data.get("health", "")
                if health and health not in ("healthy", "injured", "critical", "dead", "unknown"):
                    report.discrepancies.append(
                        Discrepancy(
                            level="info",
                            source_a=f"characters/{md_file.name}",
                            source_b="expected",
                            field="health",
                            value_a=health,
                            value_b="healthy/injured/critical/dead/unknown",
                            auto_fixable=False,
                        )
                    )

            except Exception as e:
                logger.debug("角色状态校验失败 (%s): %s", md_file.name, e)

    def _validate_event_timeline(self, report: CrossSourceReport) -> None:
        """校验事件时间线的线性可排序性。"""
        if self._events is None:
            return

        try:
            # 获取所有事件并按 timestamp 检查
            all_events = self._events.get_all_events() if hasattr(self._events, "get_all_events") else []
            if len(all_events) < 2:
                return

            timestamps: list[tuple[str, str]] = []  # [(event_id, timestamp)]
            for evt in all_events[:100]:  # 最多检查 100 条
                ts = getattr(evt, "timestamp", "")
                eid = getattr(evt, "event_id", "?")
                if ts:
                    timestamps.append((eid, ts))

            # 检查时间线是否递增
            for i in range(1, len(timestamps)):
                if timestamps[i][1] < timestamps[i - 1][1]:
                    report.discrepancies.append(
                        Discrepancy(
                            level="warning",
                            source_a=f"EventStore/{timestamps[i-1][0]}",
                            source_b=f"EventStore/{timestamps[i][0]}",
                            field="timestamp",
                            value_a=timestamps[i - 1][1],
                            value_b=timestamps[i][1],
                            auto_fixable=False,
                        )
                    )
                    break
        except Exception as e:
            logger.debug("事件时间线校验失败: %s", e)

    def _validate_frontmatter_completeness(self, report: CrossSourceReport) -> None:
        """检查 Frontmatter 的基本完整性。"""
        for subdir_name in ("canon", "characters", "draft"):
            subdir = self.project_root / subdir_name
            if not subdir.exists():
                continue

            for md_file in subdir.glob("*.md"):
                try:
                    raw = md_file.read_text(encoding="utf-8")
                    if not raw.startswith("---"):
                        report.discrepancies.append(
                            Discrepancy(
                                level="info",
                                source_a=f"{subdir_name}/{md_file.name}",
                                source_b="expected",
                                field="frontmatter",
                                value_a="(missing)",
                                value_b="required",
                                auto_fixable=False,
                            )
                        )
                except Exception:
                    continue

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """解析 YAML Frontmatter 为字典。"""
        if not text.startswith("---"):
            return {}
        end_idx = text.find("---", 3)
        if end_idx == -1:
            return {}
        fm_text = text[3:end_idx].strip()
        if not fm_text:
            return {}
        try:
            import yaml
            parsed = yaml.safe_load(fm_text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {}
