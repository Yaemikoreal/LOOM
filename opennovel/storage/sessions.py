"""
SessionStore — 会话持久化存储。

独立的 `.novel.sessions.db`，与 EventStore / MetricsStore (`.novel.db`)
隔离。管理 AI 对话历史，支持软删除 (5s 撤销窗口)。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger("opennovel.storage.sessions")

# ── SQL 初始化 ──
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT DEFAULT '',
    pinned INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    chapter_id TEXT,
    message_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT CHECK(role IN ('user','assistant','system','evaluation')),
    content TEXT DEFAULT '',
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON sessions(deleted_at);
"""


class SessionStoreError(Exception):
    """会话存储操作异常。"""


class SessionStore:
    """会话持久化存储 — 线程安全，自动建表。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    # ── 连接管理 ──

    @property
    def _conn(self) -> sqlite3.Connection:
        """线程级连接 (每个线程独立连接)。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        """初始化表结构。"""
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ── 会话 CRUD ──

    def create_session(
        self,
        project_id: str,
        title: str = "",
        chapter_id: str | None = None,
    ) -> dict[str, Any]:
        """创建新会话。返回会话元数据。"""
        session_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO sessions (id, project_id, title, chapter_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, project_id, title, chapter_id, now, now),
            )
            self._conn.commit()
        logger.info("会话创建: %s (project=%s)", session_id, project_id)
        return self._row_to_dict(session_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """按 ID 查询会话 (含软删除)。"""
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["pinned"] = bool(result["pinned"])
        result["archived"] = bool(result["archived"])
        return result

    def list_sessions(
        self,
        project_id: str,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """列出项目会话（按 updated_at DESC 排序）。"""
        where = "project_id = ? AND deleted_at IS NULL"
        params: list[Any] = [project_id]
        if include_deleted:
            where = "project_id = ?"
        rows = self._conn.execute(
            f"SELECT * FROM sessions WHERE {where} ORDER BY pinned DESC, updated_at DESC",
            params,
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["pinned"] = bool(d["pinned"])
            d["archived"] = bool(d["archived"])
            results.append(d)
        return results

    def update_session(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """更新会话字段 (title/pinned/archived/chapter_id)。"""
        allowed = {"title", "pinned", "archived", "chapter_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_session(session_id)
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        cols = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values())
        with self._lock:
            self._conn.execute(
                f"UPDATE sessions SET {cols} WHERE id = ?",
                [*vals, session_id],
            )
            self._conn.commit()
        return self.get_session(session_id)

    def soft_delete_session(self, session_id: str) -> None:
        """软删除会话 (标记 deleted_at，5s 撤销窗口)。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now, session_id),
            )
            self._conn.commit()
        logger.info("会话软删除: %s", session_id)

    def restore_session(self, session_id: str) -> bool:
        """撤销软删除 (5s 窗口内)。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT deleted_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None or row["deleted_at"] is None:
                return False
            deleted_at = datetime.fromisoformat(row["deleted_at"])
            if (datetime.now(timezone.utc) - deleted_at).total_seconds() > 5:
                return False
            self._conn.execute("UPDATE sessions SET deleted_at = NULL WHERE id = ?", (session_id,))
            self._conn.commit()
        logger.info("会话撤销删除: %s", session_id)
        return True

    def hard_delete_session(self, session_id: str) -> None:
        """物理删除会话 (含消息)。"""
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
        logger.info("会话物理删除: %s", session_id)

    # ── 消息 CRUD ──

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """添加消息到会话。"""
        msg_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else "{}"
        with self._lock:
            self._conn.execute(
                """INSERT INTO messages (id, session_id, role, content, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (msg_id, session_id, role, content, meta_json, now),
            )
            self._conn.execute(
                """UPDATE sessions
                   SET message_count = message_count + 1, updated_at = ?
                   WHERE id = ?""",
                (now, session_id),
            )
            self._conn.commit()
        return {
            "id": msg_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": now,
        }

    def list_messages(
        self,
        session_id: str,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出会话消息 (按 created_at ASC 排序)。"""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            try:
                d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                d["metadata"] = {}
            results.append(d)
        return results

    # ── 内部 ──

    def _row_to_dict(self, session_id: str) -> dict[str, Any] | None:
        return self.get_session(session_id)
