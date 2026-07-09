"""状态管理器 - 业务逻辑层（不直接操作文件系统）。

核心职责：
- 管理快照的创建与回滚（通过 YAMLStorage 和 EventStore）
- 生成状态变更的 Diff 展示
- 协调 YAML Frontmatter 与 SQLite 事件账本的一致性

铁律 3：人工审核关口。AI 只能提议状态变更，人类拥有绝对否决权。
铁律 4：操作可逆。任何破坏性写入前必须生成 Snapshot。
"""

import contextlib
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

import orjson

from opennovel.core.config import LoomConfig
from opennovel.schemas.character import CharacterFrontmatter
from opennovel.schemas.event import EventCreate, EventDiff, SnapshotMeta
from opennovel.storage.sqlite import EventStore
from opennovel.storage.yaml_storage import ConflictError, YAMLStorage

logger = logging.getLogger(__name__)

# 快照清理默认策略
DEFAULT_MAX_SNAPSHOT_COUNT = 50
DEFAULT_MAX_SNAPSHOT_DAYS = 30

# 单章 hash-only 阈值：超过 5 万字时只保存文件 hash
HASH_ONLY_THRESHOLD = 50000


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
        config: LoomConfig | None = None,
        max_count: int | None = None,
        max_days: int | None = None,
    ) -> None:
        """初始化状态管理器。

        Args:
            project_root: 项目根目录路径
            db_path: SQLite 数据库路径，默认为 project_root / ".novel.db"
            yaml_storage: YAML 存储实例，默认为 YAMLStorage()
            config: 项目配置实例，可从中读取快照保留策略
            max_count: 保留最近快照数量（覆盖 config 和默认值）
            max_days: 保留最近天数（覆盖 config 和默认值）
        """
        self.project_root = project_root
        self.snapshots_dir = project_root / ".snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._event_store: EventStore | None = None
        self._db_path = db_path or project_root / ".novel.db"
        self._yaml_storage = yaml_storage or YAMLStorage()
        self._config = config

        # 解析快照保留策略：参数 > 配置 > 默认值
        self.max_count = (
            max_count
            if max_count is not None
            else (config.snapshot_max_count if config is not None else DEFAULT_MAX_SNAPSHOT_COUNT)
        )
        self.max_days = (
            max_days
            if max_days is not None
            else (config.snapshot_max_days if config is not None else DEFAULT_MAX_SNAPSHOT_DAYS)
        )

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
                if not file_path.exists():
                    continue
                rel_path = str(file_path.relative_to(self.project_root).as_posix())
                if self._should_store_hash_only(file_path):
                    # 单章文本过长，仅保存文件 hash 以控制快照体积
                    delta_files[rel_path] = {
                        "hash_only": True,
                        "sha256": self._compute_file_hash(file_path),
                        "hint": f"单章文本超过 {HASH_ONLY_THRESHOLD} 字，仅保存文件 hash",
                    }
                else:
                    metadata, _ = self._yaml_storage.read_markdown_file(file_path)
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

            # hash-only 快照无法恢复 frontmatter，仅作明确提示
            if file_data.get("hash_only"):
                msg = f"回滚跳过 {rel_path}：该快照仅保存文件 hash，无法恢复 frontmatter"
                logger.warning(msg)
                rollback_errors.append(msg)
                continue

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

        # 清理 FTS5 索引（已回滚的文件重新分块覆盖）
        self._clean_fts5_after_rollback(delta_files)

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

    def _should_store_hash_only(self, file_path: Path) -> bool:
        """判断文件是否应采用 hash-only 快照策略。

        仅对 draft 目录下的章节正文做字数检查，超过阈值时只存 hash。

        Args:
            file_path: 待检查文件路径

        Returns:
            是否仅保存 hash
        """
        if not file_path.exists():
            return False
        # 仅对章节草稿启用该策略
        try:
            rel_parts = file_path.relative_to(self.project_root).parts
        except ValueError:
            return False
        if not rel_parts or rel_parts[0] != "draft":
            return False
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取文件失败，跳过 hash-only 判断: %s, %s", file_path, e)
            return False
        return len(text) > HASH_ONLY_THRESHOLD

    def _compute_file_hash(self, file_path: Path) -> str:
        """计算文件 SHA-256 哈希。

        Args:
            file_path: 文件路径

        Returns:
            十六进制 hash 字符串
        """
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def cleanup_snapshots(
        self,
        max_count: int | None = None,
        max_days: int | None = None,
    ) -> list[Path]:
        """清理过期快照，将其归档到 .snapshots/archive/。

        保留规则（取并集）：
        - 最近 max_count 个快照
        - 最近 max_days 天内的快照

        操作可逆：被清理的快照移动到 archive 目录，而非永久删除。

        Args:
            max_count: 保留最近快照数量，None 时使用实例配置
            max_days: 保留最近天数，None 时使用实例配置

        Returns:
            被归档的快照路径列表
        """
        effective_count = max_count if max_count is not None else self.max_count
        effective_days = max_days if max_days is not None else self.max_days

        snapshot_files = sorted(
            self.snapshots_dir.glob("*.snapshot.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not snapshot_files:
            return []

        cutoff_time = (datetime.now() - timedelta(days=effective_days)).timestamp()
        kept_by_count = set(snapshot_files[:effective_count])
        kept_by_date = {p for p in snapshot_files if p.stat().st_mtime >= cutoff_time}
        kept = kept_by_count | kept_by_date

        archive_dir = self.snapshots_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        archived: list[Path] = []
        for snap_path in snapshot_files:
            if snap_path in kept:
                continue
            target = archive_dir / snap_path.name
            # 处理文件名冲突
            counter = 1
            original_target = target
            while target.exists():
                target = original_target.with_name(
                    f"{original_target.stem}_{counter}{original_target.suffix}"
                )
                counter += 1
            snap_path.rename(target)
            archived.append(target)
            logger.info("快照已归档: %s -> %s", snap_path.name, target.name)

        return archived

    def configure_snapshot_retention(self, max_count: int, max_days: int) -> None:
        """运行时配置快照保留策略。

        Args:
            max_count: 保留最近快照数量
            max_days: 保留最近天数
        """
        self.max_count = max_count
        self.max_days = max_days
        logger.info("快照保留策略已更新: 保留最近 %d 个或 %d 天", max_count, max_days)

    def _clean_fts5_after_rollback(self, delta_files: dict) -> None:
        """回滚后清理 FTS5 索引：对涉及的文件重新分块覆盖。

        Args:
            delta_files: rollback snapshot 中的 delta_files 字典
        """
        try:
            from opennovel.core.chunker import MarkdownChunker
            from opennovel.schemas.search import ChunkSource
            from opennovel.storage.fts5 import Fts5Store

            fts5 = Fts5Store(self.project_root)
            chunker = MarkdownChunker()

            for rel_path in delta_files:
                file_path = self.project_root / rel_path
                if not file_path.exists():
                    continue
                # 根据目录推断 ChunkSource
                parent = rel_path.split("/")[0]
                source_map = {
                    "canon": ChunkSource.CANON,
                    "characters": ChunkSource.CHARACTER,
                    "draft": ChunkSource.DRAFT,
                    "subconscious": ChunkSource.SUBCONSCIOUS,
                }
                source = source_map.get(parent, ChunkSource.CANON)
                chunks = chunker.chunk_file(file_path, source, metadata={"rollback": True})
                fts5.add_chunks_batch(chunks)
                logger.debug("FTS5 已更新（回滚）: %s", rel_path)
        except Exception as e:
            logger.warning("FTS5 回滚清理失败（非致命）: %s", e)

    def safe_merge(
        self,
        file_path: str | Path,
        expected_fm: dict,
        new_fm: dict,
    ) -> bool:
        """安全合并 Frontmatter（代理 YAMLStorage.safe_merge）。

        写入前比较文件当前 Frontmatter 与预期状态是否一致，
        不一致抛出 ConflictError。回滚操作依赖此机制防止覆盖
        人类在间隙中的手动修改。

        Args:
            file_path: 文件路径
            expected_fm: 预期的当前 Frontmatter
            new_fm: 要写入的新 Frontmatter

        Returns:
            True 表示合并成功

        Raises:
            ConflictError: 文件已被外部修改
        """
        return self._yaml_storage.safe_merge(file_path, expected_fm, new_fm)


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
