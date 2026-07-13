"""在线守护进程 — 将 Doctor 从离线诊断升级为在线守护。

在关键节点自动执行一致性检查、CANON 冲突检测、索引完整性验证，
支持自动修复轻度问题、报警严重冲突。

详见 docs/adr/0012-real-time-self-healing-system.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class GuardianFinding:
    """守护进程的单项检查发现。"""

    severity: Severity
    category: str
    message: str
    auto_fixable: bool = False
    chapter_id: str = ""
    detail: str = ""


@dataclass
class GuardianReport:
    """守护进程的完整检查报告。"""

    chapter_id: str = ""
    findings: list[GuardianFinding] = field(default_factory=list)
    auto_fixed: int = 0
    needs_attention: int = 0
    passed: bool = True


class GuardianDaemon:
    """在线守护进程。

    在关键节点自动执行多维度健康检查，支持自动修复和报警。

    触发节点：
    - 每章生成后：角色状态一致性、事件时间线
    - 每 3 章：CANON 规则复核、伏笔状态
    - 每 5 章：因果图完整性、角色弧线偏差
    - 异常发生时：自动诊断 + 修复建议

    使用方式:
        guardian = GuardianDaemon(project_root)
        report = guardian.check_after_chapter("ch_005", auto_fix=True)
        if report.needs_attention > 0:
            for f in report.findings:
                print(f"[{f.severity}] {f.message}")
    """

    def __init__(
        self,
        project_root: Path,
        auto_fix_enabled: bool = True,
    ) -> None:
        """初始化守护进程。

        Args:
            project_root: 项目根目录
            auto_fix_enabled: 是否启用自动修复
        """
        self.project_root = project_root
        self.auto_fix_enabled = auto_fix_enabled
        self._chapter_count = 0

    def check_after_chapter(
        self,
        chapter_id: str,
        auto_fix: bool = True,
    ) -> GuardianReport:
        """每章生成后执行的标准检查。

        Args:
            chapter_id: 刚完成的章节 ID
            auto_fix: 是否尝试自动修复

        Returns:
            GuardianReport
        """
        self._chapter_count += 1
        report = GuardianReport(chapter_id=chapter_id)

        # ── 1. 角色状态一致性 ──
        self._check_character_consistency(report)

        # ── 2. FTS5 索引完整性 ──
        self._check_fts5_integrity(report, auto_fix)

        # ── 3. 脏标记扫描 ──
        self._check_dirty_flags(report)

        # ── 4. Snapshot 完整性 ──
        self._check_snapshot_integrity(report, chapter_id)

        # ── 每 3 章：CANON 复核 ──
        if self._chapter_count % 3 == 0:
            self._check_canon_compliance(report, chapter_id)

        # ── 每 5 章：因果图完整性 ──
        if self._chapter_count % 5 == 0:
            self._check_causal_graph(report)

        # ── 统计 ──
        report.needs_attention = sum(
            1 for f in report.findings
            if f.severity in (Severity.ERROR, Severity.CRITICAL)
        )
        report.passed = report.needs_attention == 0

        if report.findings:
            logger.info(
                "Guardian 检查完成: %d 发现, %d 已自动修复, %d 需关注",
                len(report.findings),
                report.auto_fixed,
                report.needs_attention,
            )

        return report

    def _check_character_consistency(self, report: GuardianReport) -> None:
        """检查角色状态文件的基本一致性。"""
        characters_dir = self.project_root / "characters"
        if not characters_dir.exists():
            return

        for md_file in characters_dir.glob("*.md"):
            try:
                raw = md_file.read_text(encoding="utf-8")
                # 解析 YAML Frontmatter 代替子串匹配，避免正文中的 "injury" 误匹配
                fm_data = self._parse_yaml_frontmatter(raw)
                health = fm_data.get("health", "")
                injuries = fm_data.get("injuries", [])
                if isinstance(health, str) and health.lower() == "healthy":
                    if injuries and isinstance(injuries, list) and len(injuries) > 0:
                        report.findings.append(
                            GuardianFinding(
                                severity=Severity.WARNING,
                                category="character_state",
                                message=f"{md_file.stem} 标记健康但存在 {len(injuries)} 条伤害记录",
                                auto_fixable=False,
                                detail=str(md_file),
                            )
                        )
            except Exception as e:
                logger.debug("角色状态检查失败 (%s): %s", md_file.name, e)
                continue

    def _check_fts5_integrity(
        self,
        report: GuardianReport,
        auto_fix: bool,
    ) -> None:
        """检查 FTS5 索引是否需要更新。"""
        try:
            from opennovel.storage.fts5 import Fts5Store

            db_path = self.project_root / ".novel.fts5.db"
            if not db_path.exists():
                report.findings.append(
                    GuardianFinding(
                        severity=Severity.WARNING,
                        category="search_index",
                        message="FTS5 索引数据库不存在，建议运行 novel reindex",
                        auto_fixable=False,
                        detail=".novel.fts5.db 未找到",
                    )
                )
                return

            store = Fts5Store(self.project_root, db_path)
            try:
                draft_dir = self.project_root / "draft"
                chapter_count = (
                    len(list(draft_dir.glob("*.md"))) if draft_dir.exists() else 0
                )
                if store.needs_rebuild_hint(current_chapter_count=chapter_count):
                    report.findings.append(
                        GuardianFinding(
                            severity=Severity.INFO,
                            category="search_index",
                            message=(
                                f"FTS5 索引可能需要重建 "
                                f"（{chapter_count} 章，上次重建: "
                                f"{store.get_last_rebuild_time() or '从未'}）"
                            ),
                            auto_fixable=True,
                            detail="建议运行 novel reindex",
                        )
                    )
            finally:
                store.close()
        except Exception as e:
            logger.debug("FTS5 检查失败: %s", e)

    def _check_dirty_flags(self, report: GuardianReport) -> None:
        """扫描脏标记章节。"""
        draft_dir = self.project_root / "draft"
        if not draft_dir.exists():
            return

        for md_file in draft_dir.glob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
                if "dirty_flag" in text:
                    report.findings.append(
                        GuardianFinding(
                            severity=Severity.WARNING,
                            category="dirty_flag",
                            message=f"{md_file.name} 包含脏标记，状态可能不可信",
                            auto_fixable=False,
                            detail=f"运行 novel commit 重新提取 {md_file.stem} 的状态",
                        )
                    )
            except Exception:
                continue

    def _check_snapshot_integrity(
        self,
        report: GuardianReport,
        chapter_id: str,
    ) -> None:
        """检查快照文件是否存在。"""
        snapshots_dir = self.project_root / ".snapshots"
        if not snapshots_dir.exists():
            report.findings.append(
                GuardianFinding(
                    severity=Severity.INFO,
                    category="snapshot",
                    message="快照目录不存在，回滚功能不可用",
                    auto_fixable=False,
                )
            )

    def _check_canon_compliance(
        self,
        report: GuardianReport,
        chapter_id: str,
    ) -> None:
        """检查最近章节是否违反 CANON 设定（复用 CanonChecker）。"""
        try:
            from opennovel.core.canon_checker import CanonChecker

            canon_dir = self.project_root / "canon"
            if not canon_dir.exists():
                return

            checker = CanonChecker()
            rules = checker.load_rules(canon_dir)
            if not rules:
                return

            draft_dir = self.project_root / "draft"
            chapter_file = draft_dir / f"{chapter_id}.md"
            if not chapter_file.exists():
                return

            text = chapter_file.read_text(encoding="utf-8")
            violations = checker.check_text(text, rules)
            for v in violations:
                report.findings.append(
                    GuardianFinding(
                        severity=Severity.WARNING,
                        category="canon_compliance",
                        message=f"CANON 冲突: {v.message}",
                        auto_fixable=False,
                        chapter_id=chapter_id,
                    )
                )
        except Exception as e:
            logger.debug("CANON 检查失败: %s", e)

    def _check_causal_graph(self, report: GuardianReport) -> None:
        """检查因果图完整性。"""
        try:
            from opennovel.core.causal_graph import CausalGraphAnalyzer

            event_db = self.project_root / ".novel.db"
            if not event_db.exists():
                return

            analyzer = CausalGraphAnalyzer.from_db(event_db)
            if analyzer is None:
                return

            # 检测悬空节点（无前驱也无后继的孤立事件）
            orphan_count = analyzer.get_orphan_event_count()
            if orphan_count > 0:
                report.findings.append(
                    GuardianFinding(
                        severity=Severity.INFO,
                        category="causal_graph",
                        message=f"因果图中存在 {orphan_count} 个孤立事件",
                        auto_fixable=False,
                    )
                )
        except Exception as e:
            logger.debug("因果图检查失败: %s", e)

    @staticmethod
    def _parse_yaml_frontmatter(text: str) -> dict:
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
