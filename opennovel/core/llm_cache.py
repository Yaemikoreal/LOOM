"""LLM 输入缓存 — 降低重复 LLM 调用成本。

缓存 key: (model, prompt_hash, temperature)

优先缓存场景：
- Retriever.query_canon() / query_subconscious()
- Critic.evaluate() 同一章节重复评分
- Director.analyze() 在章节数未变时的重复分析

实现：SQLite 落盘 + 内存 LRU（可选）。
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from sqlmodel import Field as SQLField
from sqlmodel import Session, SQLModel, create_engine, select

logger = logging.getLogger(__name__)


class LLMCacheEntry(SQLModel, table=True):
    """LLM 缓存条目。"""

    __tablename__ = "llm_cache"

    id: int | None = SQLField(default=None, primary_key=True)
    cache_key: str = SQLField(index=True, description="缓存键: model:prompt_hash:temperature")
    model: str = SQLField(description="模型名称")
    prompt_hash: str = SQLField(description="prompt 的 sha256 哈希")
    temperature: float = SQLField(description="生成温度")
    response_text: str = SQLField(description="缓存的 LLM 响应文本")
    response_dict: str = SQLField(default="", description="缓存的 LLM 响应字典(JSON)")
    created_at: str = SQLField(
        default_factory=lambda: __import__("datetime").datetime.now().isoformat()
    )


class LLMCache:
    """LLM 调用缓存。

    使用方式:
        cache = LLMCache(project_root / ".novel.cache.db")
        key = cache.make_key("gpt-4", messages, 0.7)
        cached = cache.get(key)
        if cached is not None:
            return cached
        response = llm_bus.chat(...)
        cache.set(key, response)
    """

    def __init__(self, db_path: Path, enabled: bool = True, max_entries: int = 1000) -> None:
        """初始化缓存。

        Args:
            db_path: SQLite 缓存数据库路径
            enabled: 是否启用缓存
            max_entries: 最大缓存条目数（超出时清理最旧条目）
        """
        self.db_path = db_path
        self.enabled = enabled
        self.max_entries = max_entries
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        SQLModel.metadata.create_all(self._engine)

    def close(self) -> None:
        """关闭数据库引擎。"""
        self._engine.dispose()

    def __enter__(self) -> "LLMCache":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @staticmethod
    def make_key(model: str, messages: list[dict[str, str]], temperature: float) -> str:
        """生成缓存键。

        Args:
            model: 模型名称
            messages: LLM 消息列表
            temperature: 生成温度

        Returns:
            缓存键字符串
        """
        prompt_text = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        return f"{model}:{prompt_hash}:{temperature}"

    def get(self, key: str) -> dict[str, Any] | None:
        """获取缓存的 LLM 响应。

        Args:
            key: 缓存键

        Returns:
            缓存的响应字典，未命中返回 None
        """
        if not self.enabled:
            return None
        try:
            with Session(self._engine) as session:
                statement = select(LLMCacheEntry).where(LLMCacheEntry.cache_key == key)
                entry = session.exec(statement).first()
                if entry is None:
                    return None
                if entry.response_dict:
                    return json.loads(entry.response_dict)
                return {"content": entry.response_text}
        except Exception as e:
            logger.debug("LLM 缓存读取失败: %s", e)
            return None

    def set(
        self,
        key: str,
        response: dict[str, Any] | Any,
        model: str = "",
        prompt_hash: str = "",
        temperature: float = 0.0,
    ) -> None:
        """写入缓存。

        Args:
            key: 缓存键
            response: LLM 响应（dict 或对象）
            model: 模型名称
            prompt_hash: prompt 哈希
            temperature: 生成温度
        """
        if not self.enabled:
            return
        try:
            response_text = ""
            response_dict = ""
            if isinstance(response, dict):
                response_dict = json.dumps(response, ensure_ascii=False)
                response_text = response.get("content", "")
            else:
                # 对象类型：尝试提取文本内容
                content = ""
                with __import__("contextlib").suppress(Exception):
                    content = response.choices[0].message.content or ""
                response_text = content
                response_dict = json.dumps({"content": content}, ensure_ascii=False)

            with Session(self._engine) as session:
                # 检查是否已存在
                statement = select(LLMCacheEntry).where(LLMCacheEntry.cache_key == key)
                existing = session.exec(statement).first()
                if existing:
                    existing.response_text = response_text
                    existing.response_dict = response_dict
                    session.add(existing)
                else:
                    entry = LLMCacheEntry(
                        cache_key=key,
                        model=model,
                        prompt_hash=prompt_hash,
                        temperature=temperature,
                        response_text=response_text,
                        response_dict=response_dict,
                    )
                    session.add(entry)
                session.commit()
                self._cleanup_if_needed(session)
        except Exception as e:
            logger.debug("LLM 缓存写入失败: %s", e)

    def _cleanup_if_needed(self, session: Session) -> None:
        """缓存条目超过上限时清理最旧的条目。"""
        try:
            count = session.exec(select(LLMCacheEntry)).all()
            if len(count) <= self.max_entries:
                return
            # 按 id 升序，删除最早的 10%
            to_delete = session.exec(
                select(LLMCacheEntry)
                .order_by(LLMCacheEntry.id)
                .limit(max(1, self.max_entries // 10))
            ).all()
            for entry in to_delete:
                session.delete(entry)
            session.commit()
        except Exception as e:
            logger.debug("LLM 缓存清理失败: %s", e)

    def invalidate(self, key: str) -> bool:
        """使指定缓存键失效。

        Args:
            key: 缓存键

        Returns:
            是否成功删除
        """
        try:
            with Session(self._engine) as session:
                statement = select(LLMCacheEntry).where(LLMCacheEntry.cache_key == key)
                entry = session.exec(statement).first()
                if entry:
                    session.delete(entry)
                    session.commit()
                    return True
        except Exception as e:
            logger.debug("LLM 缓存失效失败: %s", e)
        return False
