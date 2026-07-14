"""ToolRegistry 工具注册中心测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opennovel.core.tool_registry import ToolRegistry
from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeResult, KnowledgeSource
from opennovel.storage.metrics import MetricsStore


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
        emotional: dict = {
            "grief": 0.0,
            "anger": 0.3,
            "fear": 0.0,
            "joy": 0.0,
            "determination": 0.8,
        }
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
        self,
        project_root: Path,
        mock_retriever: MagicMock,
        mock_event_store: MagicMock,
        mock_storage: MagicMock,
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

    def test_fulfill_canon(self, project_root: Path, mock_retriever: MagicMock) -> None:
        """测试查询 canon 知识。"""
        registry = ToolRegistry(project_root=project_root, retriever=mock_retriever)
        needs = [KnowledgeNeed(concept="魔法消耗寿命", source=KnowledgeSource.CANON)]

        results = registry.fulfill(needs)
        assert len(results) == 1
        assert results[0].source == KnowledgeSource.CANON
        assert "魔法消耗寿命" in results[0].content

    def test_fulfill_character(self, project_root: Path, mock_storage: MagicMock) -> None:
        """测试查询角色状态。"""
        # 创建角色文件
        (project_root / "characters" / "char_001.md").write_text(
            "---\nid: char_001\nname: 艾伦\n---\n正文", encoding="utf-8"
        )

        registry = ToolRegistry(project_root=project_root, storage=mock_storage)
        needs = [
            KnowledgeNeed(
                concept="char_001",
                source=KnowledgeSource.CHARACTER,
                character_id="char_001",
            )
        ]

        results = registry.fulfill(needs)
        assert len(results) == 1
        assert results[0].source == KnowledgeSource.CHARACTER
        assert "艾伦" in results[0].content
        assert "左臂骨折" in results[0].content

    def test_fulfill_empty_needs(self, project_root: Path) -> None:
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
            KnowledgeNeed(
                concept="char_001",
                source=KnowledgeSource.CHARACTER,
                character_id="char_001",
            ),
        ]

        results = registry.fulfill(needs)
        assert len(results) == 2
        sources = {r.source for r in results}
        assert KnowledgeSource.CANON in sources
        assert KnowledgeSource.CHARACTER in sources

    def test_fulfill_without_retriever_returns_empty(self, project_root: Path) -> None:
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


class TestToolRegistryPermission:
    """ToolRegistry 权限治理测试。"""

    def test_writer_query_event_blocked_by_default(self, project_root: Path) -> None:
        """Writer 默认调用 query_event 应被权限表拒绝。"""
        from opennovel.core.safety_fence import SafetyFence, SafetyFenceConfig

        config = SafetyFenceConfig(
            tool_permissions={
                "writer": {
                    "allowed": ["query_canon", "query_character", "query_subconscious"],
                    "disallowed": ["query_event"],
                },
            }
        )
        fence = SafetyFence(config)
        registry = ToolRegistry(project_root=project_root)

        need = KnowledgeNeed(concept="char_001", source=KnowledgeSource.EVENT)
        result = registry.execute(need, safety_fence=fence, agent="writer")

        assert result.relevance == 0.0
        assert "权限拒绝" in result.content

    def test_critic_read_allowed_but_write_tool_not_in_whitelist(self, project_root: Path) -> None:
        """Critic 只能读取，调用非白名单工具应被拒绝。"""
        from opennovel.core.safety_fence import SafetyFence, SafetyFenceConfig

        config = SafetyFenceConfig(
            tool_permissions={
                "critic": {
                    "allowed": ["query_canon", "query_event"],
                    "disallowed": [],
                },
            }
        )
        fence = SafetyFence(config)
        registry = ToolRegistry(project_root=project_root)

        # query_character 不在 critic 白名单中，应被拒绝
        need = KnowledgeNeed(concept="char_001", source=KnowledgeSource.CHARACTER)
        result = registry.execute(need, safety_fence=fence, agent="critic")
        assert result.relevance == 0.0
        assert result.content.startswith("[权限拒绝]")

    def test_manager_write_event_allowed(self, project_root: Path) -> None:
        """Manager 在权限允许下调用 event 查询不应被拒绝。"""
        from opennovel.core.safety_fence import SafetyFence, SafetyFenceConfig

        config = SafetyFenceConfig(
            tool_permissions={
                "manager": {
                    "allowed": ["query_draft", "query_event", "write_event_store"],
                    "disallowed": ["write_canon"],
                },
            }
        )
        fence = SafetyFence(config)
        registry = ToolRegistry(project_root=project_root)

        need = KnowledgeNeed(concept="char_001", source=KnowledgeSource.EVENT)
        result = registry.execute(need, safety_fence=fence, agent="manager")
        # 允许查询，结果可能为空（无 event_store），但不应被拒绝
        assert not result.content.startswith("[权限拒绝]")


class TestToolRegistryRetry:
    """ToolRegistry 重试降级测试。"""

    def test_execute_with_retry_returns_degraded_result(self, project_root: Path) -> None:
        """handler 连续失败时返回降级结果（不抛异常）并标注检索失败。"""
        registry = ToolRegistry(project_root=project_root)
        need = KnowledgeNeed(concept="魔法", source=KnowledgeSource.CANON)

        failing_handler = MagicMock(side_effect=RuntimeError("检索服务不可用"))
        result = registry._execute_with_retry(need, failing_handler, max_retries=2)

        assert result.relevance == 0.0
        assert "检索失败" in result.content
        assert failing_handler.call_count == 2

    def test_execute_with_retry_success_on_second_attempt(self, project_root: Path) -> None:
        """handler 第二次尝试成功时返回正常结果。"""
        registry = ToolRegistry(project_root=project_root)
        need = KnowledgeNeed(concept="魔法", source=KnowledgeSource.CANON)

        success_result = KnowledgeResult(
            content="魔法消耗寿命",
            source=KnowledgeSource.CANON,
            concept="魔法",
            relevance=1.0,
        )
        handler = MagicMock(side_effect=[RuntimeError("第一次失败"), success_result])
        result = registry._execute_with_retry(need, handler, max_retries=3)

        assert result == success_result
        assert handler.call_count == 2


class TestToolRegistryAuditLog:
    """ToolRegistry 审计日志测试 (ADR 0010 Phase 3)。"""

    @pytest.fixture
    def metrics_store_with_audit(self, tmp_path: Path) -> MetricsStore:
        """创建带审计日志表的 MetricsStore。"""
        from opennovel.storage.metrics import MetricsStore

        db_path = tmp_path / "test_audit.db"
        return MetricsStore(db_path)

    def test_audit_log_table_created(
        self, metrics_store_with_audit: MetricsStore
    ) -> None:
        """验证 AuditLog 表在 MetricsStore 初始化时自动创建。"""
        from sqlmodel import Session, select

        from opennovel.schemas.metrics import AuditLog

        with Session(metrics_store_with_audit._engine) as session:
            result = session.exec(select(AuditLog)).all()
            # 空表但结构存在，不抛异常即验证通过
            assert result == []

    def test_record_and_read_audit_log(
        self, metrics_store_with_audit: MetricsStore
    ) -> None:
        """写入审计日志后能查询到。"""
        entry = metrics_store_with_audit.record_audit_log(
            agent="writer",
            tool_name="query_canon",
            source="canon",
            concept="魔法规则",
            status="success",
            duration_ms=42,
            detail="relevance=0.85",
        )
        assert entry.id is not None
        assert entry.agent == "writer"

        logs = metrics_store_with_audit.get_audit_logs(agent="writer")
        assert len(logs) == 1
        assert logs[0].tool_name == "query_canon"
        assert logs[0].status == "success"

    def test_audit_log_filter_by_status(
        self, metrics_store_with_audit: MetricsStore
    ) -> None:
        """按状态过滤审计日志。"""
        metrics_store_with_audit.record_audit_log(
            agent="writer", tool_name="t1", status="success"
        )
        metrics_store_with_audit.record_audit_log(
            agent="critic", tool_name="t2", status="denied"
        )
        metrics_store_with_audit.record_audit_log(
            agent="writer", tool_name="t3", status="error"
        )

        denied = metrics_store_with_audit.get_audit_logs(status="denied")
        assert len(denied) == 1
        assert denied[0].agent == "critic"

        errors = metrics_store_with_audit.get_audit_logs(status="error")
        assert len(errors) == 1

    def test_tool_registry_execute_writes_audit_log(
        self, project_root: Path, tmp_path: Path
    ) -> None:
        """ToolRegistry.execute() 自动写入审计日志（权限拒绝场景）。"""
        from opennovel.core.safety_fence import SafetyFence, SafetyFenceConfig
        from opennovel.storage.metrics import MetricsStore

        db_path = tmp_path / "test_tool_audit.db"
        metrics = MetricsStore(db_path)

        registry = ToolRegistry(
            project_root=project_root,
            metrics_store=metrics,
        )

        # 配置安全围栏：writer 禁止调用 query_event，白名单只允许 query_canon
        config = SafetyFenceConfig(
            enabled=True,
            tool_permissions={
                "writer": {
                    "allowed": ["query_canon"],
                    "disallowed": ["query_event"],
                }
            },
        )
        fence = SafetyFence(config)

        # 场景 1：writer 调用 query_event 应被拒绝
        need = KnowledgeNeed(concept="受伤事件", source=KnowledgeSource.EVENT)
        result = registry.execute(need, safety_fence=fence, agent="writer")

        assert "权限拒绝" in result.content
        assert result.relevance == 0.0

        # 场景 2：审计日志应记录被拒绝的调用
        denied_logs = metrics.get_audit_logs(status="denied")
        assert len(denied_logs) == 1
        assert denied_logs[0].agent == "writer"
        assert denied_logs[0].tool_name == "query_event"

    def test_tool_registry_execute_audit_success(
        self, project_root: Path, tmp_path: Path, mock_retriever: MagicMock
    ) -> None:
        """ToolRegistry.execute() 成功调用也写入审计日志。"""
        from opennovel.storage.metrics import MetricsStore

        db_path = tmp_path / "test_tool_audit_success.db"
        metrics = MetricsStore(db_path)

        registry = ToolRegistry(
            project_root=project_root,
            retriever=mock_retriever,
            metrics_store=metrics,
        )

        need = KnowledgeNeed(concept="魔法", source=KnowledgeSource.CANON)
        result = registry.execute(need, agent="writer")

        assert result is not None
        assert "魔法消耗寿命" in result.content

        # 审计日志应记录成功的调用
        success_logs = metrics.get_audit_logs(status="success")
        assert len(success_logs) == 1
        assert success_logs[0].agent == "writer"
        assert success_logs[0].tool_name == "query_canon"
        assert success_logs[0].duration_ms >= 0  # mock 调用很快，可能为 0

    def test_tool_registry_execute_audit_without_metrics(
        self, project_root: Path,
    ) -> None:
        """没有 metrics_store 时 execute() 不抛异常（后退兼容）。"""
        registry = ToolRegistry(project_root=project_root)  # metrics_store=None
        need = KnowledgeNeed(concept="魔法", source=KnowledgeSource.CANON)

        # 不传 metrics_store 时不应崩溃
        result = registry.execute(need)
        assert result is not None
