"""Actor 代理 - 沉浸式续写代理人格。

Actor 是作者的 AI 写作伙伴，核心职责：
- 根据权威分级上下文续写剧情
- 严格遵守 CANON > STATE MEMORY > SUBCONSCIOUS 的冲突降级逻辑
- 流式输出纯文本，追加到当前 Markdown 正文区
- 绝不覆盖用户正在编辑的内容
"""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from opennovel.core.context_assembler import assemble_actor_context
from opennovel.core.llm import LLMBus, extract_text_from_response
from opennovel.core.retriever import Retriever

logger = logging.getLogger(__name__)


class Actor:
    """Actor 代理 - 沉浸式续写引擎。

    使用方式:
        actor = Actor(llm_bus=bus, retriever=ret, project_root=root)
        async for chunk in actor.write_stream(chapter_path, current_text):
            print(chunk, end="")
    """

    def __init__(
        self,
        llm_bus: LLMBus,
        retriever: Retriever,
        project_root: Path,
        prompt_path: Path | None = None,
    ) -> None:
        """初始化 Actor 代理。

        Args:
            llm_bus: LLM 调用总线实例
            retriever: 语义检索路由器实例
            project_root: 项目根目录路径
            prompt_path: Actor 人格 Prompt 文件路径
        """
        self.llm_bus = llm_bus
        self.retriever = retriever
        self.project_root = project_root
        self.prompt_path = prompt_path or project_root / "prompts" / "actor.v1.md"

    def _build_context(
        self,
        chapter_path: Path,
        current_text: str,
    ) -> list[dict[str, str]]:
        """组装 Actor 的完整上下文。

        按权威分级注入：设定 > 状态 > 潜意识 > 正文。

        Args:
            chapter_path: 当前章节文件路径
            current_text: 当前正文文本

        Returns:
            组装完成的消息列表
        """
        # 从检索引擎获取设定和潜意识内容
        canon_content = self.retriever.query_canon("当前场景规则", top_k=3)
        subconscious_content = self.retriever.query_subconscious(current_text, top_k=2)

        return assemble_actor_context(
            chapter_path=chapter_path,
            project_root=self.project_root,
            current_text=current_text,
            prompt_path=self.prompt_path,
            canon_content=canon_content,
            subconscious_content=subconscious_content,
        )

    async def write_stream(
        self,
        chapter_path: Path,
        current_text: str,
    ) -> AsyncIterator[str]:
        """流式续写，逐 Token 返回生成内容。

        适用于 loom write 的交互式续写场景。

        Args:
            chapter_path: 当前章节文件路径
            current_text: 当前正文文本

        Yields:
            逐个生成的文本片段
        """
        messages = self._build_context(chapter_path, current_text)
        async for chunk in self.llm_bus.achat_stream(messages):
            yield chunk

    def write_sync(
        self,
        chapter_path: Path,
        current_text: str,
    ) -> str:
        """同步续写，一次性返回完整生成内容。

        Args:
            chapter_path: 当前章节文件路径
            current_text: 当前正文文本

        Returns:
            生成的完整文本
        """
        messages = self._build_context(chapter_path, current_text)
        response = self.llm_bus.chat(messages)
        return extract_text_from_response(response)
