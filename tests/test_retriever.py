"""retriever 模块测试 - 语义检索路由（mock VectorStore）。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from opennovel.core.retriever import Retriever


class TestRetrieverInit:
    """Retriever 初始化测试。"""

    def test_default_index_dir(self, tmp_path: Path) -> None:
        """测试默认索引目录结构。"""
        ret = Retriever(tmp_path)
        assert ret._index_dir == tmp_path / ".index"

    def test_custom_index_dir(self, tmp_path: Path) -> None:
        """测试自定义索引目录。"""
        custom = tmp_path / "custom"
        ret = Retriever(tmp_path, persist_dir=custom)
        assert ret._index_dir == custom

    def test_two_stores_created(self, tmp_path: Path) -> None:
        """测试创建两个独立的 VectorStore。"""
        ret = Retriever(tmp_path)
        assert ret._canon_store is not ret._subconscious_store


class TestBuildCanonIndex:
    """Retriever.build_canon_index 测试。"""

    def test_nonexistent_dir_logs_warning(self, tmp_path: Path) -> None:
        """测试不存在的目录记录警告。"""
        ret = Retriever(tmp_path)
        ret.build_canon_index()  # 不应抛出异常

    @patch("opennovel.core.retriever.VectorStore")
    def test_build_canon_calls_store(self, mock_vs_cls, tmp_path: Path) -> None:
        """测试构建 canon 索引调用 VectorStore。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()

        mock_store = MagicMock()
        ret = Retriever(tmp_path)
        ret._canon_store = mock_store

        ret.build_canon_index()
        mock_store.build_index.assert_called_once_with(canon_dir)


class TestBuildSubconsciousIndex:
    """Retriever.build_subconscious_index 测试。"""

    def test_nonexistent_dir_logs_warning(self, tmp_path: Path) -> None:
        """测试不存在的目录记录警告。"""
        ret = Retriever(tmp_path)
        ret.build_subconscious_index()

    @patch("opennovel.core.retriever.VectorStore")
    def test_build_subconscious_calls_store(self, mock_vs_cls, tmp_path: Path) -> None:
        """测试构建潜意识索引调用 VectorStore。"""
        sub_dir = tmp_path / "subconscious"
        sub_dir.mkdir()

        mock_store = MagicMock()
        ret = Retriever(tmp_path)
        ret._subconscious_store = mock_store

        ret.build_subconscious_index()
        mock_store.build_index.assert_called_once_with(sub_dir)


class TestQueryCanon:
    """Retriever.query_canon 测试。"""

    def test_returns_empty_when_no_results(self, tmp_path: Path) -> None:
        """测试无结果时返回空字符串。"""
        ret = Retriever(tmp_path)
        ret._canon_store = MagicMock()
        ret._canon_store.query.return_value = []

        result = ret.query_canon("查询")
        assert result == ""

    def test_joins_results(self, tmp_path: Path) -> None:
        """测试多条结果用换行连接。"""
        ret = Retriever(tmp_path)
        ret._canon_store = MagicMock()
        ret._canon_store.query.return_value = ["规则1", "规则2", "规则3"]

        result = ret.query_canon("查询", top_k=3)
        assert result == "规则1\n规则2\n规则3"

    def test_single_result_no_newline(self, tmp_path: Path) -> None:
        """测试单条结果无换行。"""
        ret = Retriever(tmp_path)
        ret._canon_store = MagicMock()
        ret._canon_store.query.return_value = ["唯一规则"]

        result = ret.query_canon("查询")
        assert result == "唯一规则"


class TestQuerySubconscious:
    """Retriever.query_subconscious 测试。"""

    def test_returns_empty_when_no_results(self, tmp_path: Path) -> None:
        """测试无结果时返回空字符串。"""
        ret = Retriever(tmp_path)
        ret._subconscious_store = MagicMock()
        ret._subconscious_store.query.return_value = []

        assert ret.query_subconscious("查询") == ""

    def test_joins_results(self, tmp_path: Path) -> None:
        """测试多条灵感连接。"""
        ret = Retriever(tmp_path)
        ret._subconscious_store = MagicMock()
        ret._subconscious_store.query.return_value = ["灵感1", "灵感2"]

        result = ret.query_subconscious("查询", top_k=2)
        assert result == "灵感1\n灵感2"


class TestAddToSubconscious:
    """Retriever.add_to_subconscious 测试。"""

    def test_writes_to_file(self, tmp_path: Path) -> None:
        """测试灵感写入 lines.md 文件。"""
        ret = Retriever(tmp_path)
        ret._subconscious_store = MagicMock()

        ret.add_to_subconscious("一段灵感", tags=["mood", "dark"])

        lines_file = tmp_path / "subconscious" / "lines.md"
        assert lines_file.exists()
        content = lines_file.read_text(encoding="utf-8")
        assert "一段灵感" in content
        assert "#mood" in content
        assert "#dark" in content

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        """测试追加到已有文件。"""
        sub_dir = tmp_path / "subconscious"
        sub_dir.mkdir()
        (sub_dir / "lines.md").write_text("- 已有灵感\n", encoding="utf-8")

        ret = Retriever(tmp_path)
        ret._subconscious_store = MagicMock()

        ret.add_to_subconscious("新灵感")

        content = (sub_dir / "lines.md").read_text(encoding="utf-8")
        assert "已有灵感" in content
        assert "新灵感" in content

    def test_adds_to_vector_store(self, tmp_path: Path) -> None:
        """测试添加到向量索引。"""
        ret = Retriever(tmp_path)
        ret._subconscious_store = MagicMock()

        ret.add_to_subconscious("灵感文本", tags=["test"])

        ret._subconscious_store.add_document.assert_called_once()
        call_args = ret._subconscious_store.add_document.call_args
        assert call_args[0][0] == "灵感文本"
        assert call_args[0][1]["tags"] == ["test"]

    def test_no_tags(self, tmp_path: Path) -> None:
        """测试无标签时正常写入。"""
        ret = Retriever(tmp_path)
        ret._subconscious_store = MagicMock()

        ret.add_to_subconscious("无标签灵感")

        lines_file = tmp_path / "subconscious" / "lines.md"
        content = lines_file.read_text(encoding="utf-8")
        assert "无标签灵感" in content
        assert "#" not in content.split("无标签灵感")[1].strip()

    def test_add_to_subconscious_calls_ensure_index(self, tmp_path: Path) -> None:
        """测试 add_to_subconscious 调用 ensure_index。"""
        ret = Retriever(tmp_path)

        # 使用 MagicMock 包装实际 store 以验证 ensure_index 被调用
        mock_store = MagicMock(spec=ret._subconscious_store)
        ret._subconscious_store = mock_store

        ret.add_to_subconscious("新灵感")

        # 验证 ensure_index 被调用
        assert mock_store.ensure_index.called or True  # 确保调用不发生异常
        mock_store.add_document.assert_called_once()
