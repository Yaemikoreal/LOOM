"""FTS5 全文索引存储适配层。

基于 SQLite FTS5 扩展实现的全文索引：
- 使用 unicode61 分词器（中文按单字 tokenize）
- 双表结构：chunks（分块元数据 + 原文）+ chunks_fts（FTS5 虚拟表，external content 模式）
- 独立数据库文件 .novel.fts5.db

FTS5 负责精确关键词匹配（专有名词、角色名、地名），
语义补位由 VectorStore 处理。

详见 docs/adr/0007-hybrid-search-and-reranking-architecture.md。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from opennovel.schemas.search import Chunk, ChunkSource

logger = logging.getLogger(__name__)

# FTS5 unicode61 分词器移除的默认标点字符
# 这些字符在中文场景下会被作为 token 分隔符，但查询端不做相同处理
_DEFAULT_TOKENCHARS = ""
_DEFAULT_SEPARATORS = ""


def _cjk_space(text: str) -> str:
    """在 CJK 字符间插入空格，使 unicode61 按单字 tokenize。

    unicode61 默认将连续 CJK 字符视为单个 token，
    插入空格后每个汉字成为独立 token，支持逐字精确匹配。
    """
    result: list[str] = []
    for ch in text:
        if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿":
            result.append(" " + ch + " ")
        else:
            result.append(ch)
    return "".join(result).strip()


class Fts5Store:
    """FTS5 全文索引管理器。

    使用 SQLite FTS5 virtual table 的 external content 模式，
    将 chunk 原文存储在 chunks 表中，FTS5 仅维护倒排索引。

    使用方式:
        store = Fts5Store(project_root)
        store.insert_chunks(chunks)
        results = store.search("影渊森林")
    """

    def __init__(self, project_root: Path, db_path: Path | None = None) -> None:
        """初始化 FTS5 存储。

        Args:
            project_root: 项目根目录路径
            db_path: 数据库文件路径，默认为 project_root / ".novel.fts5.db"
        """
        self.project_root = project_root
        self.db_path = db_path or project_root / ".novel.fts5.db"
        self._ensure_db()

    def _ensure_db(self) -> None:
        """确保数据库连接可用，创建表结构。"""
        import sqlite3

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self) -> None:
        """创建双表结构：chunks（原文）+ chunks_fts（FTS5 倒排索引）。"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                doc_stem TEXT NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                source UNINDEXED,
                doc_stem UNINDEXED,
                text,
                content='chunks',
                content_rowid='rowid',
                tokenize='unicode61'
            );

            -- 索引元数据表
            CREATE TABLE IF NOT EXISTS search_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def __enter__(self) -> Fts5Store:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── 写入操作 ──────────────────────────────────────────────────────

    def insert_chunks(self, chunks: list[Chunk]) -> int:
        """批量插入或更新 chunk。

        使用 INSERT OR REPLACE，相同 chunk_id 的旧记录被覆盖，
        实现增量更新。

        Args:
            chunks: Chunk 对象列表

        Returns:
            实际写入的行数
        """
        import json

        count = 0
        for chunk in chunks:
            # CJK 文本插入空格使 unicode61 逐字 tokenize
            indexed_text = _cjk_space(chunk.text)
            self._conn.execute(
                """INSERT OR REPLACE INTO chunks
                   (chunk_id, source, doc_stem, chunk_index, text, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    chunk.chunk_id,
                    chunk.source.value,
                    chunk.doc_stem,
                    chunk.chunk_index,
                    indexed_text,
                    json.dumps(chunk.metadata, ensure_ascii=False),
                ),
            )
            count += 1

        # FTS5 external content 模式下，chunks 表的变更会自动同步到 FTS 索引
        # 但 INSERT OR REPLACE 后需要手动触发 FTS 索引重建
        self._conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        self._conn.commit()

        logger.info("FTS5 写入完成: %d 个 chunk", count)
        return count

    def delete_by_source(self, source: ChunkSource) -> int:
        """删除指定来源的所有 chunk。

        用于重建索引时清理旧数据。

        Args:
            source: chunk 来源类型

        Returns:
            删除的行数
        """
        cursor = self._conn.execute(
            "DELETE FROM chunks WHERE source = ?", (source.value,)
        )
        deleted = cursor.rowcount  # 在后续 DML 之前立即读取
        self._conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        self._conn.commit()
        logger.info("FTS5 删除 %s 来源: %d 行", source.value, deleted)
        return deleted

    def delete_by_doc_stem(self, doc_stem: str) -> int:
        """删除指定文档的所有 chunk。

        Args:
            doc_stem: 文档名（不含扩展名）

        Returns:
            删除的行数
        """
        cursor = self._conn.execute(
            "DELETE FROM chunks WHERE doc_stem = ?", (doc_stem,)
        )
        deleted = cursor.rowcount  # 在后续 DML 之前立即读取
        self._conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        self._conn.commit()
        logger.info("FTS5 删除文档 %s: %d 个 chunk", doc_stem, deleted)
        return deleted

    def clear_all(self) -> None:
        """清空所有 chunk 数据（保留表结构）。"""
        self._conn.execute("DELETE FROM chunks")
        self._conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        self._conn.commit()
        logger.info("FTS5 已清空全部数据")

    # ── 搜索操作 ──────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 15,
        source_filter: ChunkSource | None = None,
    ) -> list[dict]:
        """执行 FTS5 全文检索。

        使用 unicode61 分词器，中文按单字 tokenize。
        FTS5 查询语法：默认将空格分隔的词转为 AND 连接。

        Args:
            query: 搜索查询文本
            top_k: 返回结果数量上限
            source_filter: 可选的来源过滤

        Returns:
            结果列表，每项含 chunk_id, source, doc_stem, text, rank
        """
        if not query.strip():
            return []

        # 构建 FTS5 查询：转义特殊字符 + CJK 逐字空格（unicode61 需要）
        _fts5_special = str.maketrans({
            '+': r'\+', '-': r'\-', '*': r'\*',
            '"': r'\"', '(': r'\(', ')': r'\)', '^': r'\^',
        })
        sanitized = query.strip().translate(_fts5_special)
        # CJK 字符间插入空格（unicode61 分词器按单字 tokenize 需要显式分隔）
        _fts5_query = _cjk_space(sanitized)

        match_clause = f"WHERE chunks_fts MATCH ?"
        params: list = [_fts5_query]

        if source_filter is not None:
            match_clause += " AND source = ?"
            params.append(source_filter.value)

        try:
            cursor = self._conn.execute(
                f"""SELECT c.chunk_id, c.source, c.doc_stem, c.text,
                           rank
                    FROM chunks_fts f
                    JOIN chunks c ON c.rowid = f.rowid
                    {match_clause}
                    ORDER BY rank
                    LIMIT ?""",
                [*params, top_k],
            )
        except Exception as e:
            logger.warning("FTS5 搜索异常（查询: '%s'）: %s", query[:100], e)
            return []

        results = []
        for row in cursor.fetchall():
            results.append(
                {
                    "chunk_id": row[0],
                    "source": row[1],
                    "doc_stem": row[2],
                    "text": row[3],
                    "rank": row[4],
                }
            )

        return results

    # ── 元数据操作 ────────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        """读取搜索元数据。

        Args:
            key: 元数据键名

        Returns:
            元数据值，不存在时返回 None
        """
        cursor = self._conn.execute(
            "SELECT value FROM search_meta WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """写入搜索元数据。

        Args:
            key: 元数据键名
            value: 元数据值
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO search_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_chunk_count(self) -> int:
        """获取当前索引的 chunk 总数。"""
        cursor = self._conn.execute("SELECT COUNT(*) FROM chunks")
        return cursor.fetchone()[0]

    def get_chunk_count_by_source(self) -> dict[str, int]:
        """按来源统计 chunk 数量。"""
        cursor = self._conn.execute(
            "SELECT source, COUNT(*) FROM chunks GROUP BY source"
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    # ── 索引生命周期 ──────────────────────────────────────────────────

    def record_rebuild(self) -> None:
        """记录全量重建的时间戳。"""
        self.set_meta("last_rebuild", str(int(time.time())))

    def get_last_rebuild_time(self) -> int | None:
        """获取上次全量重建的时间戳。

        Returns:
            时间戳（秒），从未重建时返回 None
        """
        val = self.get_meta("last_rebuild")
        return int(val) if val else None

    def needs_rebuild_hint(
        self,
        current_chapter_count: int,
        threshold_chapters: int = 5,
        threshold_days: int = 7,
    ) -> bool:
        """检查是否需要提示用户重建索引。

        条件：新增章节数超过阈值，或距上次重建超过阈值天数。

        Args:
            current_chapter_count: 当前章节总数
            threshold_chapters: 新增章节数阈值
            threshold_days: 天数阈值

        Returns:
            是否需要提示
        """
        last_count_str = self.get_meta("last_chapter_count")
        last_count = int(last_count_str) if last_count_str else 0

        if current_chapter_count - last_count >= threshold_chapters:
            return True

        last_rebuild = self.get_last_rebuild_time()
        if last_rebuild is not None:
            days_since = (time.time() - last_rebuild) / 86400
            if days_since >= threshold_days:
                return True

        return False

    def record_chapter_count(self, count: int) -> None:
        """记录当前章节总数（用于增量检测）。

        Args:
            count: 章节总数
        """
        self.set_meta("last_chapter_count", str(count))
