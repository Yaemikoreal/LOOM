"""状态管理器 - 业务逻辑层（不直接操作文件系统）。

核心职责：
- 管理快照的创建与回滚（通过 YAMLStorage 和 EventStore）
- 生成状态变更的 Diff 展示
- 协调 YAML Frontmatter 与 SQLite 事件账本的一致性

铁律 3：人工审核关口。AI 只能提议状态变更，人类拥有绝对否决权。
铁律 4：操作可逆。任何破坏性写入前必须生成 Snapshot。
"""

import contextlib
import logging
from datetime import datetime
from pathlib import Path

import orjson

from opennovel.schemas.character import CharacterFrontmatter
from opennovel.schemas.event import EventCreate, EventDiff, SnapshotMeta
from opennovel.storage.sqlite import EventStore
from opennovel.storage.yaml_storage import ConflictError, YAMLStorage

logger = logging.getLogger(__name__)


class StateManager:
    """状态管理器，协调 YAML Frontmatter 和 SQLite 事件账本。

    依赖注入：接收 YAMLStorage 和 EventStore 实例，不直接操作文件系统。

    使用方式:
        manager = StateManager(project_root)
        snapshot = manager.create_snapshot("ch_001", affected_files=[...])
    """

    def __init__(
        self,
        project_root: Path,
        db_path: Path | None = None,
        yaml_storage: YAMLStorage | None = None,
    ) -> None:
        """初始化状态管理器。

        Args:
            project_root: 项目根目录路径
            db_path: SQLite 数据库路径，默认为 project_root / ".novel.db"
            yaml_storage: YAML 存储实例，默认为 YAMLStorage()
        """
        self.project_root = project_root
        self.snapshots_dir = project_root / ".snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._event_store: EventStore | None = None
        self._db_path = db_path or project_root / ".novel.db"
        self._yaml_storage = yaml_storage or YAMLStorage()

    @property
    def event_store(self) -> EventStore:
        """懒加载事件存储实例。"""
        if self._event_store is None:
            self._event_store = EventStore(self._db_path)
        return self._event_store

    @property
    def yaml_storage(self) -> YAMLStorage:
        """返回 YAML 存储实例。"""
        return self._yaml_storage

    def create_snapshot(
        self, chapter_id: str, affected_files: list[Path] | None = None
    ) -> SnapshotMeta:
        """创建文件级增量快照，仅记录受影响文件的 Frontmatter。

        铁律 4：任何破坏性状态写入前必须生成 Snapshot。

        Args:
            chapter_id: 关联的章节 ID
            affected_files: 本次 commit 影响的文件路径列表（只 snapshot 这些文件）

        Returns:
            快照元数据
        """
        timestamp = datetime.now().isoformat()
        snapshot_id = f"snap_{chapter_id}_{int(datetime.now().timestamp())}"
        snapshot_path = self.snapshots_dir / f"{snapshot_id}.snapshot.json"

        delta_files: dict[str, dict] = {}
        if affected_files:
            for file_path in affected_files:
                if file_path.exists():
                    metadata, _ = self._yaml_storage.read_markdown_file(file_path)
                    rel_path = str(file_path.relative_to(self.project_root).as_posix())
                    delta_files[rel_path] = {
                        "fm_before": _serialize_frontmatter(metadata),
                        "fm_after": None,
                    }

        snapshot_data = {
            "snapshot_id": snapshot_id,
            "source_command": f"commit {chapter_id}",
            "timestamp": timestamp,
            "delta_files": delta_files,
            "delta_sqlite": {"event_ids_to_rollback": []},
        }

        with open(snapshot_path, "wb") as f:
            f.write(orjson.dumps(snapshot_data, option=orjson.OPT_INDENT_2))

        logger.info("快照已创建: %s (%d 个文件)", snapshot_path, len(delta_files))
        return SnapshotMeta(**snapshot_data)

    def update_snapshot_after(
        self,
        snapshot_id: str,
        affected_files: list[Path],
        events_added: list[str],
    ) -> None:
        """在 commit 完成后更新快照的 after 状态。

        Args:
            snapshot_id: 快照 ID
            affected_files: 本次 commit 影响的文件路径列表
            events_added: 新增的事件 ID 列表
        """
        snapshot_path = self.snapshots_dir / f"{snapshot_id}.snapshot.json"
        if not snapshot_path.exists():
            logger.warning("快照文件不存在: %s", snapshot_path)
            return

        with open(snapshot_path, "rb") as f:
            data = orjson.loads(f.read())

        delta_files = data.get("delta_files", {})
        for file_path in affected_files:
            if file_path.exists():
                metadata, _ = self._yaml_storage.read_markdown_file(file_path)
                rel_path = str(file_path.relative_to(self.project_root).as_posix())
                if rel_path in delta_files:
                    delta_files[rel_path]["fm_after"] = _serialize_frontmatter(metadata)

        data["delta_files"] = delta_files
        data.setdefault("delta_sqlite", {})["event_ids_to_rollback"] = events_added

        with open(snapshot_path, "wb") as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))

        logger.info("快照 after 状态已更新: %s", snapshot_id)

    def rollback_snapshot(self, snapshot_id: str) -> bool:
        """从快照恢复状态，只恢复 delta_files 中记录的文件。

        覆写前校验当前文件 Frontmatter 与 fm_after 是否一致，
        若不一致则触发 ConflictError，防止覆盖人类的外部修改。

        铁律 4：支持 loom rollback 秒级恢复。

        Args:
            snapshot_id: 要恢复的快照 ID

        Returns:
            恢复是否成功
        """
        snapshot_path = self.snapshots_dir / f"{snapshot_id}.snapshot.json"
        if not snapshot_path.exists():
            logger.error("快照文件不存在: %s", snapshot_path)
            return False

        with open(snapshot_path, "rb") as f:
            data = orjson.loads(f.read())

        delta_files = data.get("delta_files", {})
        rollback_errors: list[str] = []

        for rel_path, file_data in delta_files.items():
            file_path = self.project_root / rel_path
            fm_before = file_data.get("fm_before", {})
            fm_after = file_data.get("fm_after")

            if not file_path.exists():
                logger.warning("文件不存在，跳过: %s", rel_path)
                continue

            # 冲突检测：如果 fm_after 存在且当前文件与 fm_after 不一致
            # 说明人类在 commit 后在外部修改了该文件
            if fm_after is not None:
                try:
                    self._yaml_storage.safe_merge(
                        file_path,
                        updates=fm_before,
                        expected_current=fm_after,
                    )
                    logger.info("已恢复文件: %s", rel_path)
                except ConflictError as e:
                    msg = f"回滚跳过 {rel_path}：{e}"
                    logger.warning(msg)
                    rollback_errors.append(msg)
            else:
                # 没有 fm_after（快照异常），直接覆写 fm_before
                _, body = self._yaml_storage.read_markdown_file(file_path)
                self._yaml_storage.write_markdown_file(file_path, fm_before, body)
                logger.info("已强制恢复文件: %s", rel_path)

        # 恢复 SQLite 事件
        events_added = data.get("delta_sqlite", {}).get("event_ids_to_rollback", [])
        if events_added:
            self.event_store.delete_events_by_ids(events_added)
            logger.info("已删除 %d 条事件记录", len(events_added))

        if rollback_errors:
            logger.warning("回滚完成，%d 个文件因冲突跳过", len(rollback_errors))

        return True

    def apply_character_diff(self, character_id: str, updates: dict) -> CharacterFrontmatter:
        """将角色状态变更应用到 Frontmatter。

        Args:
            character_id: 角色 Canonical ID
            updates: 需要更新的 Frontmatter 字段字典

        Returns:
            更新后的 CharacterFrontmatter 对象

        Raises:
            FileNotFoundError: 角色文件不存在
        """
        char_path = self.project_root / "characters" / f"{character_id}.md"
        if not char_path.exists():
            raise FileNotFoundError(f"角色文件不存在: {char_path}")

        new_metadata = self._yaml_storage.update_frontmatter(char_path, updates)
        return CharacterFrontmatter(**new_metadata)

    def apply_event(self, event: EventCreate) -> None:
        """将事件写入 SQLite 事件账本。

        Args:
            event: 经过人工审核确认的事件
        """
        self.event_store.add_event(event)
        logger.info("事件已写入账本: %s", event.event_id)

    def generate_diff_text(self, diffs: list[EventDiff]) -> str:
        """生成人类可读的 Diff 文本，用于终端展示。

        Args:
            diffs: 事件变更列表

        Returns:
            格式化的 Diff 文本
        """
        lines: list[str] = []
        for diff in diffs:
            if diff.action == "add":
                lines.append(
                    f"+ [Event] {diff.event.character_id} "
                    f"{diff.event.event_type}: {diff.event.description}"
                )
            elif diff.action == "remove":
                lines.append(
                    f"- [Event] {diff.event.character_id} "
                    f"{diff.event.event_type}: {diff.event.description}"
                )
            elif diff.action == "modify" and diff.before:
                lines.append(
                    f"~ [Event] {diff.event.character_id} "
                    f"{diff.event.event_type}: "
                    f"{diff.before.description} -> {diff.event.description}"
                )
        return "\n".join(lines)

    def list_snapshots(self) -> list[SnapshotMeta]:
        """列出所有可用快照，按时间倒序排列。

        Returns:
            快照元数据列表
        """
        snapshots: list[SnapshotMeta] = []
        for snapshot_file in sorted(self.snapshots_dir.glob("*.snapshot.json"), reverse=True):
            try:
                with open(snapshot_file, "rb") as f:
                    data = orjson.loads(f.read())
                snapshots.append(SnapshotMeta(**data))
            except Exception as e:
                logger.warning("读取快照文件失败: %s, %s", snapshot_file, e)
        snapshots.sort(key=lambda s: s.timestamp, reverse=True)
        return snapshots


def _serialize_frontmatter(metadata: dict) -> dict:
    """将 Frontmatter 元数据序列化为 JSON 兼容格式。

    排除无法 JSON 序列化的类型。对 Pydantic 模型递归展开。

    Args:
        metadata: 原始 Frontmatter 字典

    Returns:
        JSON 兼容的字典
    """
    result: dict = {}
    for key, value in metadata.items():
        if hasattr(value, "model_dump"):
            result[key] = value.model_dump()
        elif isinstance(value, dict):
            result[key] = _serialize_frontmatter(value)
        elif isinstance(value, (list, str, int, float, bool, type(None))):
            result[key] = value
        else:
            with contextlib.suppress(Exception):
                result[key] = str(value)
    return result
