"""FTS5 全文索引存储 — SQLite FTS5 实现关键词精确匹配。

基于 SQLite FTS5 引擎，使用 unicode61 分词器（不引入 jieba），
按中文单字 tokenize。专有名词（角色名、地名）精确匹配。

双表设计：
- chunks: 分块元数据 + 原始文本（源表）
- chunks_fts: FTS5 全文索引（内容独立存储）

数据库文件：`.novel.fts5.db`，独立于 EventStore 和 MetricsStore。
搜索索引的生命周期与叙事真相不同（可删除重建）。

使用方式:
    fts5 = Fts5Store(project_root)
    fts5.add_chunk("canon_world-rules_p000", "影渊森林的规则...", "canon", "world-rules")
    results = fts5.search("影渊森林", top_k=10)
    for chunk_id, text, rank in results:
        print(f"[{rank}] {chunk_id}: {text[:50]}")

详见 ADR 0007 — 混合语义-关键词检索 + 重排序架构。
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from opennovel.schemas.search import Chunk

logger = logging.getLogger(__name__)

# FTS5 数据库文件名
FTS5_DB_FILENAME = ".novel.fts5.db"

# 默认的 FTS5 查询 top_k
_DEFAULT_TOP_K = 15

# 搜索元数据键
_META_KEY_VERSION = "index_version"
_META_KEY_REBUILT_AT = "rebuilt_at"
_META_KEY_TOTAL_CHUNKS = "total_chunks"


class Fts5Store:
    """FTS5 全文索引存储，管理 SQLite FTS5 索引的生命周期。

    支持实时增量更新（commit/stash 时触发），
    以及全量重建（novel reindex）。

    线程安全说明：SQLite 连接非线程安全。Fts5Store 实例
    不应在多个线程间共享。每线程应使用独立的 Fts5Store 实例。
    """

    def __init__(self, project_root: Path) -> None:
        """初始化 FTS5 存储。

        Args:
            project_root: 项目根目录路径
        """
        self.project_root = project_root
        self.db_path = project_root / FTS5_DB_FILENAME
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── 数据库连接管理 ─────────────────────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        """获取数据库连接（惰性初始化）。"""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Fts5Store":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── 数据库初始化 ───────────────────────────────────────────────

    def _init_db(self) -> None:
        """初始化数据库表结构。

        建表语句使用 IF NOT EXISTS，可安全重复调用。
        """
        self.conn.executescript("""
            -- 分块元数据表（源表）
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                doc_stem TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            -- FTS5 全文索引（内容独立存储）
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                text,
                tokenize='unicode61'
            );

            -- 索引元数据表
            CREATE TABLE IF NOT EXISTS search_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self.conn.commit()

    # ── 写入操作 ──────────────────────────────────────────────────

    def add_chunk(
        self,
        chunk_id: str,
        text: str,
        source: str,
        doc_stem: str,
        metadata: dict | None = None,
    ) -> None:
        """添加一个分块到索引（增量更新）。

        Args:
            chunk_id: 分块 ID
            text: 分块文本
            source: 来源类型（canon/character/event/subconscious/draft）
            doc_stem: 文档标识短名
            metadata: 附加元数据
        """
        now = datetime.now().isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        self.conn.execute(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, source, doc_stem, text, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chunk_id, source, doc_stem, text, meta_json, now),
        )
        # 同步更新 FTS5 索引（CJK 逐字分词）
        fts5_text = self._cjk_space(text)
        self.conn.execute(
            """INSERT OR REPLACE INTO chunks_fts (chunk_id, text)
               VALUES (?, ?)""",
            (chunk_id, fts5_text),
        )
        self.conn.commit()

    def add_chunks_batch(self, chunks: list[Chunk]) -> int:
        """批量添加分块到索引。

        Args:
            chunks: 分块列表

        Returns:
            成功添加的分块数
        """
        now = datetime.now().isoformat()
        count = 0
        for chunk in chunks:
            try:
                meta_json = json.dumps(chunk.metadata, ensure_ascii=False)
                self.conn.execute(
                    """INSERT OR REPLACE INTO chunks
                       (chunk_id, source, doc_stem, text, metadata, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (chunk.chunk_id, chunk.source.value, chunk.metadata.get("doc_stem", "doc"),
                     chunk.text, meta_json, now),
                )
                fts5_text = self._cjk_space(chunk.text)
                self.conn.execute(
                    """INSERT OR REPLACE INTO chunks_fts (chunk_id, text)
                       VALUES (?, ?)""",
                    (chunk.chunk_id, fts5_text),
                )
                count += 1
            except Exception as e:
                logger.warning("添加分块 %s 失败: %s", chunk.chunk_id, e)
        self.conn.commit()
        logger.info("FTS5 批量添加 %d/%d 分块", count, len(chunks))
        return count

    def incremental_update_file(
        self, file_path: Path, source: str, metadata: dict | None = None,
    ) -> int:
        """增量更新单个文件的 FTS5 索引（分块 → 批量写入）。

        供 CLI commit/stash 使用，消除 import/chunk/add 模式的重复。

        Args:
            file_path: Markdown 文件路径
            source: 来源类型字符串（canon/character/draft/subconscious）
            metadata: 附加元数据

        Returns:
            写入的分块数
        """
        from opennovel.core.chunker import MarkdownChunker
        from opennovel.schemas.search import ChunkSource

        source_enum = (
            ChunkSource(source)
            if source in {s.value for s in ChunkSource}
            else ChunkSource.CANON
        )
        chunker = MarkdownChunker()
        chunks = chunker.chunk_file(file_path, source_enum, metadata)
        return self.add_chunks_batch(chunks)

    def incremental_update_text(
        self, text: str, source: str, doc_stem: str, metadata: dict | None = None,
    ) -> int:
        """增量更新文本的 FTS5 索引。

        Args:
            text: 文本内容
            source: 来源类型字符串
            doc_stem: 文档标识短名
            metadata: 附加元数据

        Returns:
            写入的分块数
        """
        from opennovel.core.chunker import MarkdownChunker
        from opennovel.schemas.search import ChunkSource

        source_enum = (
            ChunkSource(source)
            if source in {s.value for s in ChunkSource}
            else ChunkSource.CANON
        )
        chunker = MarkdownChunker()
        chunks = chunker.chunk_document(text, source_enum, doc_stem, metadata)
        return self.add_chunks_batch(chunks)

    def delete_chunk(self, chunk_id: str) -> None:
        """从索引中删除一个分块。

        Args:
            chunk_id: 分块 ID
        """
        self.conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (chunk_id,))
        self.conn.execute(
            "INSERT INTO chunks_fts(chunks_fts, rowid, chunk_id, text) "
            "SELECT 'delete', rowid, ?, '' FROM chunks_fts WHERE chunk_id = ?",
            (chunk_id, chunk_id),
        )
        self.conn.commit()

    def delete_document(self, doc_stem: str) -> int:
        """删除指定文档的所有分块。

        Args:
            doc_stem: 文档标识短名

        Returns:
            删除的分块数
        """
        chunk_ids = [
            row["chunk_id"]
            for row in self.conn.execute(
                "SELECT chunk_id FROM chunks WHERE doc_stem = ?", (doc_stem,)
            ).fetchall()
        ]
        for cid in chunk_ids:
            self.delete_chunk(cid)
        return len(chunk_ids)

    def clear_all(self) -> None:
        """清空所有索引数据（用于全量重建前的清理）。"""
        self.conn.executescript("""
            DELETE FROM chunks;
            INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild');
            DELETE FROM search_meta;
        """)
        self.conn.commit()

    # ── 查询操作 ──────────────────────────────────────────────────

    def search(
        self,
        query_text: str,
        top_k: int = _DEFAULT_TOP_K,
    ) -> list[dict[str, Any]]:
        """执行 FTS5 全文检索。

        Args:
            query_text: 查询文本（FTS5 查询语法，中文按单字匹配）
            top_k: 返回结果数

        Returns:
            检索结果列表，每项含 chunk_id、text、source、doc_stem、metadata、rank、score
        """
        if not query_text or not query_text.strip():
            return []

        fts5_query = self._build_fts5_query(query_text)
        if not fts5_query:
            return []

        try:
            rows = self.conn.execute(
                """SELECT c.chunk_id, c.text, c.source, c.doc_stem, c.metadata,
                          bm25(chunks_fts) AS score
                   FROM chunks_fts
                   LEFT JOIN chunks c ON chunks_fts.chunk_id = c.chunk_id
                   WHERE chunks_fts MATCH ?
                   ORDER BY score
                   LIMIT ?""",
                (fts5_query, top_k),
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("FTS5 查询语法错误: %s (query=%s)", e, fts5_query)
            return []

        results: list[dict[str, Any]] = []
        for rank, row in enumerate(rows):
            results.append({
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "source": row["source"],
                "doc_stem": row["doc_stem"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "rank": rank,
                "score": float(row["score"]),
            })

        return results

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """根据 chunk_id 查询分块详情。

        Args:
            chunk_id: 分块 ID

        Returns:
            分块数据字典，不存在返回 None
        """
        row = self.conn.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "chunk_id": row["chunk_id"],
            "text": row["text"],
            "source": row["source"],
            "doc_stem": row["doc_stem"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "created_at": row["created_at"],
        }

    def get_chunks_by_doc(self, doc_stem: str) -> list[dict[str, Any]]:
        """查询指定文档的所有分块。

        Args:
            doc_stem: 文档标识短名

        Returns:
            分块列表
        """
        rows = self.conn.execute(
            "SELECT * FROM chunks WHERE doc_stem = ? ORDER BY chunk_id",
            (doc_stem,),
        ).fetchall()
        return [
            {
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "source": row["source"],
                "doc_stem": row["doc_stem"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # ── 元数据管理 ─────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        """获取索引元数据。

        Args:
            key: 元数据键名

        Returns:
            元数据值，不存在返回 None
        """
        row = self.conn.execute(
            "SELECT value FROM search_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """设置索引元数据。

        Args:
            key: 元数据键名
            value: 元数据值
        """
        self.conn.execute(
            "INSERT OR REPLACE INTO search_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def update_rebuild_meta(self, total_chunks: int) -> None:
        """更新全量重建后的元数据。

        Args:
            total_chunks: 重建后的总分块数
        """
        now = datetime.now().isoformat()
        self.set_meta(_META_KEY_VERSION, "1")
        self.set_meta(_META_KEY_REBUILT_AT, now)
        self.set_meta(_META_KEY_TOTAL_CHUNKS, str(total_chunks))

    def get_rebuild_info(self) -> dict[str, Any]:
        """获取索引状态信息。

        Returns:
            包含 rebuilt_at、total_chunks 等信息的字典
        """
        rebuilt_at = self.get_meta(_META_KEY_REBUILT_AT)
        total_chunks = self.get_meta(_META_KEY_TOTAL_CHUNKS)
        return {
            "rebuilt_at": rebuilt_at or "never",
            "total_chunks": int(total_chunks) if total_chunks else 0,
        }

    def get_total_chunk_count(self) -> int:
        """获取当前索引中的分块总数。

        Returns:
            分块总数
        """
        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM chunks").fetchone()
        return row["cnt"] if row else 0

    # ── 辅助方法 ──────────────────────────────────────────────────

    @staticmethod
    def _cjk_space(text: str) -> str:
        """在连续 CJK 字符之间插入空格，使 unicode61 按字分词。

        unicode61 不会自动拆分 CJK 连续字符（"曙光计划"被当作一个 token）。
        手动在每两个 CJK 字符间插入空格后，每个汉字成为独立 token，
        支持按单字搜索和前缀匹配。

        Args:
            text: 原始文本

        Returns:
            中文单字间插入空格后的文本
        """
        result: list[str] = []
        prev_cjk = False
        for char in text:
            is_cjk = not char.isascii() and char.isalpha()
            if prev_cjk and is_cjk:
                result.append(" ")
            result.append(char)
            prev_cjk = is_cjk
        return "".join(result)

    @staticmethod
    def _build_fts5_query(query_text: str) -> str:
        """将用户查询转换为 FTS5 查询表达式。

        unicode61 分词器下，需要手动对 CJK 字符插入空格才能按字检索。
        策略：
        1. 对中文连续文本逐字插入空格（"曙光计划" → "曙 光 计 划"）
        2. 保留字母数字作为完整 token
        3. 去除标点和特殊符号

        最终的每个 token 在 FTS5 默认语法下按 AND 匹配。

        Args:
            query_text: 原始查询文本

        Returns:
            FTS5 查询表达式字符串
        """
        if not query_text.strip():
            return ""

        # 1. CJK 逐字拆分
        spaced = Fts5Store._cjk_space(query_text.strip())

        # 2. 只保留字母、数字、中文字符、空格
        cleaned: list[str] = []
        for char in spaced:
            if char.isalnum() or char.isspace():
                cleaned.append(char)
        return "".join(cleaned).strip()

    def check_staleness(self, chapter_count: int) -> tuple[bool, str | None]:
        """检查索引是否过时。

        VectorStore 脏数据遗忘缓解（ADR 0007）：
        - 新增 5+ 章或距上次重建超 7 天时提示重建

        Args:
            chapter_count: 当前项目章节数

        Returns:
            (is_stale, message) 元组，过时时消息非空
        """
        rebuilt_at = self.get_meta(_META_KEY_REBUILT_AT)
        if rebuilt_at is None:
            return True, "索引从未重建，建议执行 novel reindex"

        from datetime import datetime, timezone

        try:
            rebuilt = datetime.fromisoformat(rebuilt_at)
            now = datetime.now(timezone.utc).astimezone()
            if rebuilt.tzinfo is None:
                rebuilt = rebuilt.replace(tzinfo=now.tzinfo)
            days_since = (now - rebuilt).days

            # 检查章数增长（ADR 0007：新增 5+ 章时提示重建）
            old_chapters_str = self.get_meta(_META_KEY_TOTAL_CHUNKS)
            if old_chapters_str and chapter_count > 0:
                old_chapters = int(old_chapters_str)
                new_chapters = chapter_count - old_chapters
                if new_chapters >= 5:
                    return True, (
                        f"自上次索引后新增了 {new_chapters} 章（>=5 章），"
                        f"建议执行 novel reindex 以保持搜索索引最新"
                    )

            if days_since >= 7:
                return True, (
                    f"索引已 {days_since} 天未重建（>7 天），"
                    f"建议执行 novel reindex 以保持向量语义最新"
                )
        except (ValueError, TypeError):
            pass

        return False, None
