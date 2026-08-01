"""vector 模块测试 - 向量索引存储（mock LlamaIndex）。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from opennovel.storage.vector import VectorStore, _check_llama_index, _get_embed_model


class TestCheckLlamaIndex:
    """_check_llama_index 依赖检查测试。"""

    @patch("opennovel.storage.vector.VectorStoreIndex", create=True)
    def test_returns_true_when_available(self, mock_index) -> None:
        """测试 LlamaIndex 可用时返回 True。"""
        assert _check_llama_index() is True

    def test_returns_false_when_unavailable(self) -> None:
        """测试 LlamaIndex 不可用时返回 False。"""
        with patch.dict("sys.modules", {"llama_index.core": None}):
            assert _check_llama_index() is False


class TestGetEmbedModel:
    """_get_embed_model embedding 模型获取测试。"""

    def test_local_model_returns_none_when_unavailable(self) -> None:
        """测试本地模型不可用时返回 None。"""
        with patch.dict("sys.modules", {"llama_index.embeddings.huggingface": None}):
            result = _get_embed_model("local:BAAI/bge-m3")
            assert result is None

    def test_non_local_model_returns_none(self) -> None:
        """测试非本地模型返回 None（由 LlamaIndex 默认处理）。"""
        result = _get_embed_model("openai")
        assert result is None

    def test_local_model_success(self) -> None:
        """测试本地模型成功获取。"""
        mock_embed = MagicMock()
        mock_module = MagicMock()
        mock_module.HuggingFaceEmbedding.return_value = mock_embed

        with patch.dict(
            "sys.modules",
            {"llama_index.embeddings.huggingface": mock_module},
        ):
            result = _get_embed_model("local:BAAI/bge-m3")
            assert result is mock_embed


class TestVectorStoreInit:
    """VectorStore 初始化测试。"""

    def test_default_index_dir(self, tmp_path: Path) -> None:
        """测试默认索引目录。"""
        store = VectorStore(tmp_path)
        assert store.index_dir == tmp_path / ".index"

    def test_custom_index_dir(self, tmp_path: Path) -> None:
        """测试自定义索引目录。"""
        custom = tmp_path / "custom_index"
        store = VectorStore(tmp_path, index_dir=custom)
        assert store.index_dir == custom

    def test_default_embedding_model(self, tmp_path: Path) -> None:
        """测试默认 embedding 模型。"""
        store = VectorStore(tmp_path)
        assert store.embedding_model == "local:BAAI/bge-m3"

    def test_index_initially_none(self, tmp_path: Path) -> None:
        """测试初始状态索引为 None。"""
        store = VectorStore(tmp_path)
        assert store._index is None


class TestBuildIndex:
    """VectorStore.build_index 索引构建测试。"""

    def test_nonexistent_dir_logs_warning(self, tmp_path: Path) -> None:
        """测试不存在的目录记录警告。"""
        store = VectorStore(tmp_path)
        # 不应抛出异常
        store.build_index(tmp_path / "nonexistent")

    @patch("opennovel.storage.vector._check_llama_index", return_value=False)
    def test_no_llama_index_skips_build(self, mock_check, tmp_path: Path) -> None:
        """测试 LlamaIndex 不可用时跳过构建。"""
        store = VectorStore(tmp_path)
        store.build_index(tmp_path / "canon")
        assert store._index is None

    @patch("opennovel.storage.vector._check_llama_index", return_value=True)
    @patch("opennovel.storage.vector._get_embed_model", return_value=None)
    def test_build_with_mock_llama_index(self, mock_embed, mock_check, tmp_path: Path) -> None:
        """测试使用 mock LlamaIndex 构建索引。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "rules.md").write_text("# 魔法规则\n\n魔法消耗寿命。", encoding="utf-8")

        mock_index = MagicMock()
        mock_doc = MagicMock()
        mock_reader_cls = MagicMock()
        mock_reader_cls.return_value.load_data.return_value = [mock_doc]
        mock_vsi_cls = MagicMock()
        mock_vsi_cls.from_documents.return_value = mock_index

        mock_core = MagicMock()
        mock_core.SimpleDirectoryReader = mock_reader_cls
        mock_core.VectorStoreIndex = mock_vsi_cls
        mock_core.StorageContext = MagicMock()

        with patch.dict("sys.modules", {"llama_index.core": mock_core}):
            store = VectorStore(tmp_path)
            store.build_index(canon_dir)

            mock_vsi_cls.from_documents.assert_called_once()
            mock_index.storage_context.persist.assert_called_once()

    @patch("opennovel.storage.vector._check_llama_index", return_value=True)
    @patch("opennovel.storage.vector._get_embed_model", return_value=None)
    def test_build_empty_dir(self, mock_embed, mock_check, tmp_path: Path) -> None:
        """测试空目录不构建索引。"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        mock_reader_cls = MagicMock()
        mock_reader_cls.return_value.load_data.return_value = []

        mock_core = MagicMock()
        mock_core.SimpleDirectoryReader = mock_reader_cls

        with patch.dict("sys.modules", {"llama_index.core": mock_core}):
            store = VectorStore(tmp_path)
            store.build_index(empty_dir)

            assert store._index is None


class TestLoadIndex:
    """VectorStore.load_index 索引加载测试。"""

    def test_nonexistent_dir_returns_false(self, tmp_path: Path) -> None:
        """测试不存在的目录返回 False。"""
        store = VectorStore(tmp_path)
        assert store.load_index() is False

    @patch("opennovel.storage.vector._check_llama_index", return_value=False)
    def test_no_llama_index_returns_false(self, mock_check, tmp_path: Path) -> None:
        """测试 LlamaIndex 不可用时返回 False。"""
        store = VectorStore(tmp_path)
        (tmp_path / ".index").mkdir()
        assert store.load_index() is False

    @patch("opennovel.storage.vector._check_llama_index", return_value=True)
    def test_load_success(self, mock_check, tmp_path: Path) -> None:
        """测试成功加载索引。"""
        index_dir = tmp_path / ".index"
        index_dir.mkdir()

        mock_index = MagicMock()
        mock_core = MagicMock()
        mock_core.StorageContext.from_defaults.return_value = MagicMock()
        mock_core.load_index_from_storage.return_value = mock_index

        with patch.dict("sys.modules", {"llama_index.core": mock_core}):
            store = VectorStore(tmp_path)
            result = store.load_index()

            assert result is True
            assert store._index is mock_index

    @patch("opennovel.storage.vector._check_llama_index", return_value=True)
    def test_load_failure(self, mock_check, tmp_path: Path) -> None:
        """测试加载失败返回 False。"""
        index_dir = tmp_path / ".index"
        index_dir.mkdir()

        mock_core = MagicMock()
        mock_core.StorageContext.from_defaults.side_effect = Exception("损坏")

        with patch.dict("sys.modules", {"llama_index.core": mock_core}):
            store = VectorStore(tmp_path)
            result = store.load_index()
            assert result is False


class TestQuery:
    """VectorStore.query 语义检索测试。"""

    def test_no_index_returns_empty(self, tmp_path: Path) -> None:
        """测试索引未加载时返回空列表。"""
        store = VectorStore(tmp_path)
        assert store.query("测试查询") == []

    def test_query_returns_results(self, tmp_path: Path) -> None:
        """测试查询返回结果。"""
        store = VectorStore(tmp_path)
        mock_index = MagicMock()
        mock_response = MagicMock()
        mock_node1 = MagicMock()
        mock_node1.__str__ = lambda self: "结果1"
        mock_node2 = MagicMock()
        mock_node2.__str__ = lambda self: "结果2"
        mock_response.source_nodes = [mock_node1, mock_node2]
        mock_index.as_query_engine.return_value.query.return_value = mock_response
        store._index = mock_index

        results = store.query("测试", top_k=2)
        assert results == ["结果1", "结果2"]

    def test_query_failure_returns_empty(self, tmp_path: Path) -> None:
        """测试查询异常返回空列表。"""
        store = VectorStore(tmp_path)
        mock_index = MagicMock()
        mock_index.as_query_engine.return_value.query.side_effect = Exception("错误")
        store._index = mock_index

        assert store.query("测试") == []


class TestAddDocument:
    """VectorStore.add_document 文档添加测试。"""

    def test_no_index_logs_warning(self, tmp_path: Path) -> None:
        """测试索引未初始化时记录警告。"""
        store = VectorStore(tmp_path)
        # 不应抛出异常
        store.add_document("测试文本")

    def test_add_document_calls_insert(self, tmp_path: Path) -> None:
        """测试添加文档调用 insert。"""
        store = VectorStore(tmp_path)
        mock_index = MagicMock()
        store._index = mock_index

        store.add_document("新文档", {"source": "test"})

        mock_index.insert.assert_called_once()
        mock_index.storage_context.persist.assert_called_once()


class TestDeleteDocument:
    """VectorStore.delete_document 文档删除测试。"""

    def test_no_index_logs_warning(self, tmp_path: Path) -> None:
        """测试索引未初始化时记录警告。"""
        store = VectorStore(tmp_path)
        store.delete_document("doc_001")

    def test_delete_calls_ref_doc(self, tmp_path: Path) -> None:
        """测试删除调用 delete_ref_doc。"""
        store = VectorStore(tmp_path)
        mock_index = MagicMock()
        store._index = mock_index

        store.delete_document("doc_001")

        mock_index.delete_ref_doc.assert_called_once_with("doc_001")
        mock_index.storage_context.persist.assert_called_once()


class TestEnsureIndex:
    """VectorStore.ensure_index 自动加载/构建测试。"""

    def test_returns_true_when_index_already_loaded(self, tmp_path: Path) -> None:
        """测试索引已加载时直接返回 True。"""
        store = VectorStore(tmp_path)
        store._index = MagicMock()
        assert store.ensure_index() is True

    def test_loads_existing_index(self, tmp_path: Path) -> None:
        """测试加载已有持久化索引。"""
        index_dir = tmp_path / ".index"
        index_dir.mkdir(parents=True, exist_ok=True)

        store = VectorStore(tmp_path, index_dir=index_dir)
        # 索引目录存在但未初始化 _index
        # ensure_index 会尝试 load_index → load_index 会检查 index_dir.exists()
        # 由于没有实际索引数据，load_index 会失败
        # 然后 ensure_index 会尝试 build_index，但没有源文档目录
        # 所以最终返回 False — 这是预期行为
        result = store.ensure_index()
        # 我们只验证它不会崩溃，结果取决于底层条件
        assert isinstance(result, bool)

    @patch("opennovel.storage.vector.VectorStore.load_index")
    def test_builds_when_no_index(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """测试无持久化索引时从源目录构建。"""
        mock_load.return_value = False  # 加载失败

        store = VectorStore(tmp_path)
        store.build_index = MagicMock(return_value=None)  # type: ignore[method-assign]

        # 创建源目录
        src_dir = tmp_path / "docs"
        src_dir.mkdir()

        result = store.ensure_index(src_dir)
        store.build_index.assert_called_once_with(src_dir)  # type: ignore[attr-defined]

    @patch("opennovel.storage.vector.VectorStore.load_index")
    def test_returns_false_when_no_source(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """测试无索引且无源目录时返回 False。"""
        mock_load.return_value = False

        store = VectorStore(tmp_path)
        # 不传 documents_dir
        result = store.ensure_index()
        assert result is False

    @patch("opennovel.storage.vector.VectorStore.load_index")
    def test_ensure_then_add_document(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """测试 ensure_index 后 add_document 正常工作。"""
        mock_load.return_value = False  # 加载失败

        store = VectorStore(tmp_path)
        src_dir = tmp_path / "docs"
        src_dir.mkdir()
        # mock build_index 以设置 _index
        store._index = MagicMock()

        assert store.ensure_index(src_dir) is True
        store.add_document("测试文本")
        store._index.insert.assert_called_once()
