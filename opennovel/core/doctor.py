"""世界观健康度诊断器。

扫描整个项目，检测结构性问题：
- 孤立角色：存在但未被任何章节引用
- 悬空引用：章节引用了不存在的角色
- ID 一致性：文件名与 Frontmatter id 不匹配
- 脏标记扫描：存在 dirty_flag 的章节
- 事件账本健康：事件引用了不存在的角色
- 快照统计：可用快照数量和最新时间

纯规则检测，不依赖 LLM。V1 实现，为 V2 Weaver 代理留出接口。
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from opennovel.core.config import LoomConfig
from opennovel.storage.metrics import MetricsStore
from opennovel.storage.sqlite import EventStore
from opennovel.storage.yaml_storage import YAMLStorage

logger = logging.getLogger(__name__)


class DiagnosticLevel(str, Enum):
    """诊断结果级别。"""

    OK = "OK"  # 正常
    INFO = "INFO"  # 信息性提示
    WARNING = "WARNING"  # 需要关注
    ERROR = "ERROR"  # 严重问题


@dataclass
class DiagnosticItem:
    """诊断结果项。"""

    level: DiagnosticLevel
    category: str  # orphan / dangling_ref / id_mismatch / dirty / ledger
    message: str  # 人类可读的描述
    details: str = ""  # 详细信息


class Doctor:
    """世界观健康度诊断器。

    使用方式:
        doctor = Doctor(project_root)
        items = doctor.diagnose()
        for item in items:
            print(f"[{item.level}] {item.message}")
    """

    def __init__(
        self,
        project_root: Path,
        yaml_storage: YAMLStorage | None = None,
    ) -> None:
        """初始化诊断器。

        Args:
            project_root: 项目根目录路径
            yaml_storage: YAML 存储实例，默认为 YAMLStorage()
        """
        self.project_root = project_root
        self._yaml_storage = yaml_storage or YAMLStorage()

    def diagnose(self) -> list[DiagnosticItem]:
        """执行全部诊断检查。

        Returns:
            诊断结果列表
        """
        items: list[DiagnosticItem] = []

        # 收集所有角色 ID 和章节引用
        all_characters = self._scan_characters()
        chapter_refs = self._scan_chapter_references()

        # 1. 孤立角色检测
        items.extend(self._check_orphan_characters(all_characters, chapter_refs))

        # 2. 悬空引用检测
        items.extend(self._check_dangling_references(all_characters, chapter_refs))

        # 3. ID 一致性检测
        items.extend(self._check_id_consistency())

        # 4. 脏标记扫描
        items.extend(self._check_dirty_flags())

        # 5. 事件账本健康
        items.extend(self._check_event_ledger(all_characters))

        # 6. 快照统计
        items.extend(self._check_snapshots())

        return items

    def _scan_characters(self) -> dict[str, Path]:
        """扫描所有角色文件，返回 {character_id: file_path}。"""
        chars_dir = self.project_root / "characters"
        if not chars_dir.exists():
            return {}

        characters: dict[str, Path] = {}
        for char_file in chars_dir.glob("*.md"):
            try:
                meta, _ = self._yaml_storage.read_markdown_file(char_file)
                char_id = meta.get("id")
                if char_id and isinstance(char_id, str):
                    characters[char_id] = char_file
            except Exception as e:  # noqa: BLE001
                logger.warning("读取角色文件失败: %s, %s", char_file, e)

        return characters

    def _scan_chapter_references(self) -> set[str]:
        """扫描所有章节中引用的角色 ID 集合。"""
        draft_dir = self.project_root / "draft"
        if not draft_dir.exists():
            return set()

        refs: set[str] = set()
        for chapter_file in draft_dir.glob("*.md"):
            try:
                meta, _ = self._yaml_storage.read_markdown_file(chapter_file)
                # POV 角色
                pov = meta.get("pov")
                if pov and isinstance(pov, str):
                    refs.add(pov)
                # 活跃角色
                active = meta.get("active_characters", [])
                if isinstance(active, list):
                    for cid in active:
                        if isinstance(cid, str):
                            refs.add(cid)
            except Exception as e:  # noqa: BLE001
                logger.warning("读取章节文件失败: %s, %s", chapter_file, e)

        return refs

    def _check_orphan_characters(
        self,
        all_characters: dict[str, Path],
        chapter_refs: set[str],
    ) -> list[DiagnosticItem]:
        """检测孤立角色：存在但未被任何章节引用。"""
        items: list[DiagnosticItem] = []
        for char_id in all_characters:
            if char_id not in chapter_refs:
                items.append(
                    DiagnosticItem(
                        level=DiagnosticLevel.WARNING,
                        category="orphan_character",
                        message=f"角色 {char_id} 未被任何章节引用，可能是孤立角色",
                        details=f"文件: {all_characters[char_id]}",
                    )
                )
        return items

    def _check_dangling_references(
        self,
        all_characters: dict[str, Path],
        chapter_refs: set[str],
    ) -> list[DiagnosticItem]:
        """检测悬空引用：章节引用了不存在的角色。"""
        items: list[DiagnosticItem] = []
        for ref_id in chapter_refs:
            if ref_id not in all_characters:
                items.append(
                    DiagnosticItem(
                        level=DiagnosticLevel.WARNING,
                        category="dangling_reference",
                        message=f"引用了不存在的角色 {ref_id}",
                        details="请检查章节文件的 pov 和 active_characters 字段",
                    )
                )
        return items

    def _check_id_consistency(self) -> list[DiagnosticItem]:
        """检测文件名与 Frontmatter id 的一致性。"""
        items: list[DiagnosticItem] = []

        for subdir in ["characters", "draft", "canon"]:
            dir_path = self.project_root / subdir
            if not dir_path.exists():
                continue

            for md_file in dir_path.glob("*.md"):
                try:
                    meta, _ = self._yaml_storage.read_markdown_file(md_file)
                except Exception:  # noqa: BLE001
                    continue

                frontmatter_id = meta.get("id")
                if not frontmatter_id or not isinstance(frontmatter_id, str):
                    continue

                # 文件名（不含扩展名）应与 id 匹配
                file_stem = md_file.stem
                if file_stem != frontmatter_id:
                    items.append(
                        DiagnosticItem(
                            level=DiagnosticLevel.WARNING,
                            category="id_mismatch",
                            message=(
                                f"{subdir}/{md_file.name}: 文件名 '{file_stem}' "
                                f"与 Frontmatter id '{frontmatter_id}' 不一致"
                            ),
                            details="建议重命名文件以匹配 id，或更新 Frontmatter id",
                        )
                    )

        return items

    def _check_dirty_flags(self) -> list[DiagnosticItem]:
        """扫描所有章节中的脏标记。"""
        items: list[DiagnosticItem] = []
        draft_dir = self.project_root / "draft"
        if not draft_dir.exists():
            return items

        for chapter_file in sorted(draft_dir.glob("*.md")):
            try:
                meta, _ = self._yaml_storage.read_markdown_file(chapter_file)
            except Exception:  # noqa: BLE001
                continue

            dirty_flag = meta.get("dirty_flag")
            if dirty_flag:
                items.append(
                    DiagnosticItem(
                        level=DiagnosticLevel.WARNING,
                        category="dirty_flag",
                        message=f"章节 {chapter_file.stem} 存在脏标记 (dirty_flag={dirty_flag})",
                        details="建议重新执行 loom commit 清除脏标记",
                    )
                )

        return items

    def _check_event_ledger(self, all_characters: dict[str, Path]) -> list[DiagnosticItem]:
        """检测事件账本中引用不存在角色的事件。"""
        items: list[DiagnosticItem] = []
        db_path = self.project_root / ".novel.db"
        if not db_path.exists():
            return items

        try:
            store = EventStore(db_path)
            all_events = store.get_all_events()
        except Exception as e:
            logger.warning("读取事件账本失败: %s", e)
            return items

        for event in all_events:
            if event.character_id not in all_characters:
                items.append(
                    DiagnosticItem(
                        level=DiagnosticLevel.WARNING,
                        category="ledger_orphan",
                        message=(f"事件 {event.event_id} 引用了不存在的角色 {event.character_id}"),
                        details=f"章节: {event.chapter_id}, 类型: {event.event_type}",
                    )
                )

        return items

    def _check_snapshots(self) -> list[DiagnosticItem]:
        """统计快照信息。"""
        items: list[DiagnosticItem] = []
        snap_dir = self.project_root / ".snapshots"
        if not snap_dir.exists():
            items.append(
                DiagnosticItem(
                    level=DiagnosticLevel.INFO,
                    category="snapshot_stats",
                    message="快照目录不存在，尚未执行过 loom commit",
                )
            )
            return items

        snap_files = list(snap_dir.glob("*.snapshot.json"))
        count = len(snap_files)

        if count == 0:
            items.append(
                DiagnosticItem(
                    level=DiagnosticLevel.INFO,
                    category="snapshot_stats",
                    message="暂无快照",
                )
            )
        else:
            # 找到最新快照
            latest = max(snap_files, key=lambda f: f.stat().st_mtime)
            items.append(
                DiagnosticItem(
                    level=DiagnosticLevel.OK,
                    category="snapshot_stats",
                    message=f"共 {count} 个快照，最新: {latest.stem}",
                )
            )

        return items

    def generate_dashboard(self) -> dict:
        """生成项目健康面板数据。

        Returns:
            包含各面板区块的字典:
            {
                "config": {"status": "...", "model": "...", ...},
                "progress": {"chapters": "...", "total": "...", ...},
                "critic": {"total": N, "avg_score": N, ...},
                "diagnosis": {"errors": N, "warnings": N, ...},
            }
        """
        dashboard: dict = {}

        # ── 配置面板 ──
        try:
            cfg = LoomConfig.load(self.project_root)
            model_valid = bool(cfg.model)
            dashboard["config"] = {
                "status": "valid" if model_valid else "missing_model",
                "model": cfg.model or "未配置",
                "token_budget": cfg.token_budget,
                "output_reserve": cfg.output_reserve,
                "has_api_key": bool(cfg.api_key),
                "version": cfg.version,
                "creative_direction": cfg.creative_direction or "",
                "director_enabled": cfg.director_enabled,
            }
        except Exception as e:
            dashboard["config"] = {"status": "error", "error": str(e)}

        # ── 章节进度面板 ──
        draft_dir = self.project_root / "draft"
        if draft_dir.exists():
            chapters = sorted(draft_dir.glob("ch_*.md"))
            dashboard["progress"] = {
                "chapters": len(chapters),
                "files": [c.stem for c in chapters],
                "latest": chapters[-1].stem if chapters else "",
            }
        else:
            dashboard["progress"] = {"chapters": 0, "files": [], "latest": ""}

        # ── 角色统计面板 ──
        chars = self._scan_characters()
        dashboard["characters"] = {
            "total": len(chars),
            "ids": list(chars.keys()),
        }

        # ── 事件统计面板 ──
        try:
            store = EventStore(self.project_root / ".novel.db")
            events = store.get_all_events()
            dashboard["events"] = {
                "total": len(events),
                "types": list({e.event_type for e in events}),
            }
        except Exception:
            dashboard["events"] = {"total": 0, "types": []}

        # ── 评分趋势面板（仅当 metrics.db 存在时） ──
        metrics_path = self.project_root / ".novel.metrics.db"
        if metrics_path.exists():
            try:
                ms = MetricsStore(metrics_path)
                history = ms.get_evaluation_history()
                if history:
                    scores = [r.total_score for r in history]
                    avg_score = sum(scores) / len(scores)
                    latest_score = scores[-1]
                    dashboard["critic"] = {
                        "total": len(history),
                        "avg_score": round(avg_score, 1),
                        "latest_score": latest_score,
                        "is_pass": latest_score >= 80 if history else None,
                    }
                else:
                    dashboard["critic"] = {"total": 0}
            except Exception:
                dashboard["critic"] = {"total": 0, "error": "读取失败"}
        else:
            dashboard["critic"] = {"total": 0}

        # ── 诊断摘要面板 ──
        items = self.diagnose()
        errors = sum(1 for i in items if i.level == DiagnosticLevel.ERROR)
        warnings = sum(1 for i in items if i.level == DiagnosticLevel.WARNING)
        info = sum(1 for i in items if i.level == DiagnosticLevel.INFO)
        dashboard["diagnosis"] = {
            "total": len(items),
            "errors": errors,
            "warnings": warnings,
            "info": info,
            "items": [
                {
                    "level": i.level.value,
                    "category": i.category,
                    "message": i.message,
                    "details": i.details,
                }
                for i in items
            ],
        }

        return dashboard
