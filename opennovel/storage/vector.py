"""向量索引存储适配层。

基于 LlamaIndex 实现本地向量索引管理：
- 索引的构建与持久化（支持 canon/ 和 subconscious/ 目录）
- 增量更新（添加/删除文档）
- 语义检索（按相似度返回 top_k 结果）

Embedding 后端：
- 默认：LlamaIndex 内置 embedding（OpenAI 等）
- 可选：sentence-transformers 本地 BGE-M3（pip install loom-narrative[local-embedding]）
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _check_llama_index() -> bool:
    """检查 llama_index 是否可用。"""
    try:
        from llama_index.core import VectorStoreIndex  # noqa: F401

        return True
    except ImportError:
        logger.error("llama_index 未安装，请执行: pip install llama-index")
        return False


def _get_embed_model(model_name: str = "local:BAAI/bge-m3"):
    """获取 embedding 模型实例。

    Args:
        model_name: 模型名称。"local:xxx" 表示本地 sentence-transformers 模型，
                    其他值由 LlamaIndex 自行解析（如 OpenAI）。

    Returns:
        embedding 模型实例，若不可用则返回 None
    """
    if model_name.startswith("local:"):
        local_name = model_name.removeprefix("local:")
        try:
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding

            return HuggingFaceEmbedding(model_name=local_name)
        except ImportError:
            logger.warning(
                "sentence-transformers 未安装，无法使用本地 embedding %s。"
                "请执行: pip install sentence-transformers",
                local_name,
            )
            return None
    # 非本地模型由 LlamaIndex 默认处理
    return None


class VectorStore:
    """向量索引存储，管理 LlamaIndex 向量索引的生命周期。

    使用方式:
        store = VectorStore(project_root)
        store.build_index(project_root / "canon")
        results = store.query("魔法规则", top_k=3)
    """

    def __init__(
        self,
        project_root: Path,
        index_dir: Path | None = None,
        embedding_model: str = "local:BAAI/bge-m3",
    ) -> None:
        """初始化向量存储。

        Args:
            project_root: 项目根目录路径
            index_dir: 索引持久化目录，默认为 project_root / ".index"
            embedding_model: Embedding 模型名称
        """
        self.project_root = project_root
        self.index_dir = index_dir or project_root / ".index"
        self.embedding_model = embedding_model
        self._index = None

    def build_index(self, documents_dir: Path) -> None:
        """从 Markdown 文档目录构建向量索引。

        扫描目录下所有 .md 文件，构建 LlamaIndex 向量索引并持久化。

        Args:
            documents_dir: 文档目录路径（如 canon/ 或 subconscious/）
        """
        if not _check_llama_index():
            return
        if not documents_dir.exists():
            logger.warning("文档目录不存在: %s", documents_dir)
            return

        from llama_index.core import (
            SimpleDirectoryReader,
            VectorStoreIndex,
        )

        # 只读取 .md 文件
        documents = SimpleDirectoryReader(
            input_dir=str(documents_dir),
            recursive=True,
            required_exts=[".md"],
        ).load_data()

        if not documents:
            logger.info("目录中无 .md 文件: %s", documents_dir)
            return

        # 获取 embedding 模型
        embed_model = _get_embed_model(self.embedding_model)

        # 构建索引
        kwargs = {}
        if embed_model is not None:
            kwargs["embed_model"] = embed_model
        else:
            logger.info("无本地 embedding，使用 LlamaIndex 默认 embedding 模型")

        try:
            self._index = VectorStoreIndex.from_documents(documents, **kwargs)
        except Exception as e:
            logger.error("向量索引构建失败（embedding 模型不可用）: %s", e)
            return

        # 持久化
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._index.storage_context.persist(persist_dir=str(self.index_dir))
        logger.info("向量索引构建完成: %s (%d 个文档)", documents_dir, len(documents))

    def load_index(self) -> bool:
        """从持久化目录加载已有索引。

        Returns:
            是否成功加载索引
        """
        if not _check_llama_index():
            return False
        if not self.index_dir.exists():
            logger.warning("索引目录不存在: %s", self.index_dir)
            return False

        try:
            from llama_index.core import StorageContext, load_index_from_storage

            storage_context = StorageContext.from_defaults(persist_dir=str(self.index_dir))
            self._index = load_index_from_storage(storage_context)
            logger.info("向量索引加载成功: %s", self.index_dir)
            return True
        except Exception as e:
            logger.error("向量索引加载失败: %s", e)
            return False

    def query(self, query_text: str, top_k: int = 3) -> list[str]:
        """执行语义检索查询。

        Args:
            query_text: 查询文本
            top_k: 返回最相关的 top_k 条结果

        Returns:
            检索结果文本列表，按相关度降序
        """
        if self._index is None:
            logger.warning("索引未加载，返回空结果")
            return []

        try:
            query_engine = self._index.as_query_engine(similarity_top_k=top_k)
            response = query_engine.query(query_text)
            return [str(node) for node in response.source_nodes]
        except Exception as e:
            logger.error("语义检索失败: %s", e)
            return []

    def ensure_index(self, documents_dir: Path | None = None) -> bool:
        """确保索引可用：优先加载已有索引，否则从源目录构建。

        Args:
            documents_dir: 源文档目录（当需要构建时使用）

        Returns:
            索引是否可用
        """
        if self._index is not None:
            return True

        # 尝试加载已有索引
        if self.load_index():
            return True

        # 索引不存在且有源目录时自动构建
        if documents_dir is not None and documents_dir.exists():
            try:
                self.build_index(documents_dir)
            except Exception as e:
                logger.warning("索引自动构建失败: %s", e)
            return self._index is not None

        logger.warning("索引不可用: 无持久化索引且未提供源目录")
        return False

    def add_document(self, text: str, metadata: dict | None = None) -> None:
        """向索引中添加单个文档，触发增量更新。

        Args:
            text: 文档文本
            metadata: 文档元数据
        """
        if self._index is None:
            logger.warning("索引未初始化，无法添加文档")
            return

        try:
            from llama_index.core import Document

            doc = Document(text=text, metadata=metadata or {})
            self._index.insert(doc)
            # 持久化更新
            self._index.storage_context.persist(persist_dir=str(self.index_dir))
            logger.info("文档已添加到索引")
        except Exception as e:
            logger.error("添加文档失败: %s", e)

    def delete_document(self, doc_id: str) -> None:
        """从索引中删除指定文档。

        Args:
            doc_id: 文档 ID
        """
        if self._index is None:
            logger.warning("索引未初始化，无法删除文档")
            return

        try:
            self._index.delete_ref_doc(doc_id)
            self._index.storage_context.persist(persist_dir=str(self.index_dir))
            logger.info("文档已从索引删除: %s", doc_id)
        except Exception as e:
            logger.error("删除文档失败: %s", e)
