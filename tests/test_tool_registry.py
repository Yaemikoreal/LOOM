"""ToolRegistry 工具注册中心测试。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opennovel.core.tool_registry import ToolRegistry
from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeResult, KnowledgeSource


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """临时项目根目录。"""
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "characters").mkdir()
    (root / "canon").mkdir()
    return root


@pytest.fixture
def mock_retriever() -> MagicMock:
    ret = MagicMock()
    ret.query_canon.return_value = "魔法消耗寿命，每人最多使用三次。"
    ret.query_subconscious.return_value = "一个关于魔法起源的梦境碎片。"
    return ret


@pytest.fixture
def mock_event_store() -> MagicMock:
    store = MagicMock()
    store.get_events_by_character.return_value = []
    store.get_high_pressure_events.return_value = []
    return store


@pytest.fixture
def mock_storage() -> MagicMock:
    storage = MagicMock()
    # 模拟 CharacterFile(frontmatter=CharacterFrontmatter(...), body="...") 的行为
    from pydantic import BaseModel

    class FakeFrontmatter(BaseModel):
        id: str = "char_001"
        name: str = "艾伦"
        physical: dict = {"injuries": ["左臂骨折"], "buffs": [], "debuffs": []}
        emotional: dict = {"grief": 0.0, "anger": 0.3, "fear": 0.0, "joy": 0.0, "determination": 0.8}
        location: str = "迷雾森林"

    class FakeCharacterFile:
        frontmatter = FakeFrontmatter()
        body = ""

    storage.read_character_file.return_value = FakeCharacterFile()
    return storage


class TestKnowledgeSchema:
    """KnowledgeNeed / KnowledgeResult schema 测试。"""

    def test_knowledge_need_minimal(self) -> None:
        """测试 KnowledgeNeed 最小字段。"""
        need = KnowledgeNeed(concept="魔法设定", source=KnowledgeSource.CANON)
        assert need.concept == "魔法设定"
        assert need.source == KnowledgeSource.CANON
        assert need.context == ""
        assert need.character_id == ""

    def test_knowledge_need_full(self) -> None:
        """测试 KnowledgeNeed 全部字段。"""
        need = KnowledgeNeed(
            concept="char_001",
            source=KnowledgeSource.CHARACTER,
            context="需要角色当前状态",
            character_id="char_001",
        )
        assert need.character_id == "char_001"
        assert need.context == "需要角色当前状态"

    def test_knowledge_result_default_relevance(self) -> None:
        """测试 KnowledgeResult 默认相关性。"""
        result = KnowledgeResult(
            content="测试内容",
            source=KnowledgeSource.CANON,
            concept="测试",
        )
        assert result.relevance == 1.0

    def test_knowledge_result_custom_relevance(self) -> None:
        """测试 KnowledgeResult 自定义相关性。"""
        result = KnowledgeResult(
            content="测试内容",
            source=KnowledgeSource.SUBCONSCIOUS,
            concept="测试",
            relevance=0.5,
        )
        assert result.relevance == 0.5

    def test_knowledge_source_enum_values(self) -> None:
        """测试 KnowledgeSource 枚举值。"""
        assert KnowledgeSource.CANON.value == "canon"
        assert KnowledgeSource.SUBCONSCIOUS.value == "subconscious"
        assert KnowledgeSource.CHARACTER.value == "character"
        assert KnowledgeSource.EVENT.value == "event"


class TestToolRegistry:
    """ToolRegistry 功能测试。"""

    def test_init_with_all_deps(
        self, project_root: Path, mock_retriever: MagicMock, mock_event_store: MagicMock, mock_storage: MagicMock
    ) -> None:
        """测试完整初始化。"""
        registry = ToolRegistry(
            project_root=project_root,
            retriever=mock_retriever,
            event_store=mock_event_store,
            storage=mock_storage,
        )
        assert registry.is_source_available(KnowledgeSource.CANON)
        assert registry.is_source_available(KnowledgeSource.CHARACTER)
        assert registry.is_source_available(KnowledgeSource.EVENT)

    def test_init_without_deps(self, project_root: Path) -> None:
        """测试无依赖时也可初始化。"""
        registry = ToolRegistry(project_root=project_root)
        assert registry.is_source_available(KnowledgeSource.CANON)
        # 没有 retriever 时查询返回空结果

    def test_fulfill_canon(
        self, project_root: Path, mock_retriever: MagicMock
    ) -> None:
        """测试查询 canon 知识。"""
        registry = ToolRegistry(project_root=project_root, retriever=mock_retriever)
        needs = [KnowledgeNeed(concept="魔法消耗寿命", source=KnowledgeSource.CANON)]

        results = registry.fulfill(needs)
        assert len(results) == 1
        assert results[0].source == KnowledgeSource.CANON
        assert "魔法消耗寿命" in results[0].content

    def test_fulfill_character(
        self, project_root: Path, mock_storage: MagicMock
    ) -> None:
        """测试查询角色状态。"""
        # 创建角色文件
        (project_root / "characters" / "char_001.md").write_text(
            "---\nid: char_001\nname: 艾伦\n---\n正文", encoding="utf-8"
        )

        registry = ToolRegistry(project_root=project_root, storage=mock_storage)
        needs = [KnowledgeNeed(
            concept="char_001",
            source=KnowledgeSource.CHARACTER,
            character_id="char_001",
        )]

        results = registry.fulfill(needs)
        assert len(results) == 1
        assert results[0].source == KnowledgeSource.CHARACTER
        assert "艾伦" in results[0].content
        assert "左臂骨折" in results[0].content

    def test_fulfill_empty_needs(
        self, project_root: Path
    ) -> None:
        """测试空需求列表返回空结果。"""
        registry = ToolRegistry(project_root=project_root)
        results = registry.fulfill([])
        assert results == []

    def test_fulfill_multiple_sources(
        self, project_root: Path, mock_retriever: MagicMock, mock_storage: MagicMock
    ) -> None:
        """测试同时查询多个来源。"""
        registry = ToolRegistry(
            project_root=project_root,
            retriever=mock_retriever,
            storage=mock_storage,
        )

        # 创建角色文件
        (project_root / "characters" / "char_001.md").write_text(
            "---\nid: char_001\nname: 艾伦\n---\n正文", encoding="utf-8"
        )

        needs = [
            KnowledgeNeed(concept="魔法设定", source=KnowledgeSource.CANON),
            KnowledgeNeed(concept="char_001", source=KnowledgeSource.CHARACTER, character_id="char_001"),
        ]

        results = registry.fulfill(needs)
        assert len(results) == 2
        sources = {r.source for r in results}
        assert KnowledgeSource.CANON in sources
        assert KnowledgeSource.CHARACTER in sources

    def test_fulfill_without_retriever_returns_empty(
        self, project_root: Path
    ) -> None:
        """测试没有 Retriever 时返回空结果。"""
        registry = ToolRegistry(project_root=project_root)
        needs = [KnowledgeNeed(concept="魔法", source=KnowledgeSource.CANON)]
        results = registry.fulfill(needs)
        assert len(results) == 1
        assert results[0].relevance == 0.0

    def test_get_available_sources(self, project_root: Path) -> None:
        """测试获取可用数据源。"""
        registry = ToolRegistry(project_root=project_root)
        sources = registry.get_available_sources()
        assert "canon" in sources
        assert "character" in sources

    def test_is_source_available(self, project_root: Path) -> None:
        """测试来源可用性检查。"""
        registry = ToolRegistry(project_root=project_root)
        assert registry.is_source_available(KnowledgeSource.CANON) is True
        assert registry.is_source_available(KnowledgeSource.SUBCONSCIOUS) is True
