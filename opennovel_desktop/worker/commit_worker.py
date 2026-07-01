"""CommitWorker — 提交执行器（后台线程）。

封装 Auditor 事件提取 + StateManager 快照，通过 Signal 回传结果。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot


class CommitWorker(QObject):
    """提交执行器，运行在独立 QThread。

    流程：snapshot → auditor extract → diff → 结果回传。
    """

    snapshot_created = Signal(str)  # snapshot_id
    events_extracted = Signal(list)  # list[EventCreate dict]
    commit_ready = Signal(dict)  # {files, events, diffs}
    error_occurred = Signal(str)
    rescue_mode = Signal()  # 三次提取失败，进入救援模式

    def __init__(self, project_root: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._chapter_id: str = ""

    @Slot()
    def do_commit(self, chapter_id: str) -> None:
        """执行完整提交流程。"""
        self._chapter_id = chapter_id
        try:
            # 1. Snapshot
            snapshot_id = self._create_snapshot(chapter_id)
            self.snapshot_created.emit(snapshot_id)
        except Exception as e:
            self.error_occurred.emit(f"快照失败: {e!s}")
            return

        try:
            # 2. Auditor extract (最多 3 次)
            events = self._extract_events(chapter_id)
            self.events_extracted.emit(events)

            # 3. 准备 Diff 数据
            diff_data = self._prepare_diff(chapter_id, events)
            self.commit_ready.emit(diff_data)
        except RuntimeError as rescue:
            self.rescue_mode.emit()
            self.error_occurred.emit(f"提取失败: {rescue!s}")

    def _create_snapshot(self, chapter_id: str) -> str:
        """创建文件快照。"""
        from opennovel.core.state_manager import StateManager  # noqa: PLC0415

        project_root = Path(self._project_root)
        sm = StateManager(project_root=project_root)
        meta = sm.create_snapshot(chapter_id)
        return meta.snapshot_id if hasattr(meta, "snapshot_id") else "unknown"

    def _extract_events(self, chapter_id: str) -> list[dict]:
        """调用 Auditor 提取事件（含最多 3 次重试）。"""
        from opennovel.agents.auditor import Auditor  # noqa: PLC0415
        from opennovel.core.config import LoomConfig  # noqa: PLC0415

        project_root = Path(self._project_root)
        config = LoomConfig.load(project_root)
        auditor = Auditor(project_root=project_root, config=config)

        for attempt in range(3):
            try:
                events = auditor.extract_events(chapter_id)
                if isinstance(events, list) and len(events) > 0:
                    return events
            except Exception:
                if attempt == 2:
                    raise RuntimeError("Auditor 连续 3 次提取失败") from None
                continue

        return []

    def _prepare_diff(self, chapter_id: str, events: list) -> dict:
        """准备 Diff 数据。"""
        from opennovel.core.state_manager import StateManager  # noqa: PLC0415

        project_root = Path(self._project_root)
        _ = StateManager(project_root=project_root)  # noqa: F841

        # 获取受影响文件列表
        draft_path = project_root / "draft" / f"{chapter_id}.md"
        char_dir = project_root / "characters"

        affected = []
        if draft_path.exists():
            affected.append(str(draft_path))
        for char_file in sorted(char_dir.glob("*.md")):
            affected.append(str(char_file))

        return {
            "chapter_id": chapter_id,
            "files": affected,
            "events": events,
            "file_changes": [str(p) for p in affected],
        }
