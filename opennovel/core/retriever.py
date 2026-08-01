"""LlamaIndex 检索路由 - 语义检索与向量索引管理。

基于 LlamaIndex 实现：
- 设定文档的语义检索 (CANON 层)
- 潜意识池的灵感召回 (SUBCONSCIOUS 层)
- 增量索引构建与更新

使用方式:
    retriever = Retriever(project_root)
    retriever.build_canon_index()
    canon = retriever.query_canon("魔法规则", top_k=3)
"""

import logging
from pathlib import Path

from opennovel.storage.vector import VectorStore

logger = logging.getLogger(__name__)


class Retriever:
    """语义检索路由器，管理两个独立的向量索引。

    支持两种索引类型：
    - canon: 设定文档索引，用于 CANON 层的权威设定检索
    - subconscious: 潜意识池索引，用于灵感碎片的语义召回
    """

    def __init__(
        self,
        project_root: Path,
        persist_dir: Path | None = None,
        embedding_model: str = "local:BAAI/bge-m3",
    ) -> None:
        """初始化检索路由器。

        Args:
            project_root: 项目根目录路径
            persist_dir: 索引持久化目录，默认为 project_root / ".index"
            embedding_model: Embedding 模型名称
        """
        self.project_root = project_root
        self._index_dir = persist_dir or project_root / ".index"
        self._embedding_model = embedding_model

        # 两个独立的 VectorStore 实例
        self._canon_store = VectorStore(
            project_root,
            index_dir=self._index_dir / "canon",
            embedding_model=embedding_model,
        )
        self._subconscious_store = VectorStore(
            project_root,
            index_dir=self._index_dir / "subconscious",
            embedding_model=embedding_model,
        )

    def build_canon_index(self) -> None:
        """构建设定文档的语义索引。

        扫描 canon/ 目录下的所有 Markdown 文件，构建向量索引。
        """
        canon_dir = self.project_root / "canon"
        if not canon_dir.exists():
            logger.warning("设定目录不存在: %s", canon_dir)
            return
        self._canon_store.build_index(canon_dir)

    def build_subconscious_index(self) -> None:
        """构建潜意识池的语义索引。

        扫描 subconscious/ 目录下的所有文件，构建增量向量索引。
        """
        sub_dir = self.project_root / "subconscious"
        if not sub_dir.exists():
            logger.warning("潜意识目录不存在: %s", sub_dir)
            return
        self._subconscious_store.build_index(sub_dir)

    def query_canon(self, query_text: str, top_k: int = 3) -> str:
        """从设定索引中检索相关世界观规则。

        Args:
            query_text: 查询文本，通常基于当前场景描述
            top_k: 返回最相关的 top_k 条结果

        Returns:
            检索到的设定文本，多条结果以换行分隔；索引未加载时返回空字符串
        """
        results = self._canon_store.query(query_text, top_k=top_k)
        return "\n".join(results) if results else ""

    def query_subconscious(self, query_text: str, top_k: int = 2) -> str:
        """从潜意识池中检索相似的灵感碎片。

        Args:
            query_text: 查询文本，通常基于当前正文
            top_k: 返回最相似的 top_k 条结果

        Returns:
            检索到的灵感文本，多条结果以换行分隔；索引未加载时返回空字符串
        """
        results = self._subconscious_store.query(query_text, top_k=top_k)
        return "\n".join(results) if results else ""

    def add_to_subconscious(self, text: str, tags: list[str] | None = None) -> None:
        """向潜意识池添加新的灵感碎片，并触发增量索引更新。

        同时写入 subconscious/lines.md 文件和向量索引。
        如果索引未初始化，自动从 subconscious/ 目录构建。

        Args:
            text: 灵感文本
            tags: 标签列表，用于分类和过滤
        """
        sub_dir = self.project_root / "subconscious"
        sub_dir.mkdir(parents=True, exist_ok=True)

        # 写入 Markdown 文件
        lines_file = sub_dir / "lines.md"
        tag_str = " ".join(f"#{t}" for t in (tags or []))
        entry = f"- {text} {tag_str}\n"

        with open(lines_file, "a", encoding="utf-8") as f:
            f.write(entry)

        # 确保索引可用（自动加载或构建）
        self._subconscious_store.ensure_index(sub_dir)

        # 增量添加到向量索引
        metadata = {"source": "subconscious", "tags": tags or []}
        self._subconscious_store.add_document(text, metadata)
        logger.info("灵感已存入潜意识池: %s", text[:50])
