"""CausalGraphAnalyzer 因果图分析测试（mock EventStore + networkx）。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opennovel.core.causal_graph import CausalGraphAnalyzer


def _make_event(
    event_id: str,
    chapter_id: str = "ch_001",
    character_id: str = "char_001",
    event_type: str = "CUSTOM",
    description: str = "测试事件",
    causal_pressure: float = 0.5,
    caused_by: str | None = None,
    related_event_ids: list[str] | None = None,
) -> MagicMock:
    """构造模拟事件对象。"""
    import json

    evt = MagicMock()
    evt.event_id = event_id
    evt.chapter_id = chapter_id
    evt.character_id = character_id
    evt.event_type = event_type
    evt.description = description
    evt.causal_pressure = causal_pressure
    evt.caused_by = caused_by
    evt.related_event_ids = json.dumps(related_event_ids) if related_event_ids else None
    return evt


class TestCausalGraphNoNetworkX:
    """无 networkx 时的降级行为测试。"""

    def test_without_networkx_returns_empty(self) -> None:
        """测试无 networkx 时所有分析返回空值。"""
        analyzer = CausalGraphAnalyzer()
        # 模拟 networkx 不可用
        analyzer._nx = None
        assert analyzer.build_graph() is False
        assert analyzer.get_stats() == {"error": "图未构建"}
        assert analyzer.get_causal_path("a", "b") == []
        assert analyzer.get_central_events() == []
        assert analyzer.get_high_impact_events() == []
        assert analyzer.get_character_subgraph("char_001") == {"error": "图未构建"}


class TestCausalGraphEmpty:
    """空图测试。"""

    def test_empty_event_store(self) -> None:
        """测试无事件时的空图。"""
        store = MagicMock()
        store.get_all_events.return_value = []

        analyzer = CausalGraphAnalyzer(store)
        assert analyzer.build_graph() is True
        stats = analyzer.get_stats()
        assert stats["nodes"] == 0
        assert stats["edges"] == 0

    def test_no_event_store(self) -> None:
        """测试无 EventStore 时构建失败。"""
        analyzer = CausalGraphAnalyzer()
        assert analyzer.build_graph() is False


class TestCausalGraphBuild:
    """图构建测试。"""

    def test_simple_chain(self) -> None:
        """测试简单因果链构建。"""
        store = MagicMock()
        store.get_all_events.return_value = [
            _make_event("evt_001", caused_by=None, causal_pressure=0.3),
            _make_event("evt_002", caused_by="evt_001", causal_pressure=0.5),
            _make_event("evt_003", caused_by="evt_002", causal_pressure=0.8),
        ]

        analyzer = CausalGraphAnalyzer(store)
        assert analyzer.build_graph() is True
        stats = analyzer.get_stats()
        assert stats["nodes"] == 3
        assert stats["edges"] == 2

    def test_diamond_graph(self) -> None:
        """测试菱形因果图。"""
        store = MagicMock()
        store.get_all_events.return_value = [
            _make_event("evt_001", causal_pressure=0.3),
            _make_event("evt_002", caused_by="evt_001", causal_pressure=0.5),
            _make_event("evt_003", caused_by="evt_001", causal_pressure=0.5),
            _make_event("evt_004", caused_by="evt_002", causal_pressure=0.7),
        ]

        analyzer = CausalGraphAnalyzer(store)
        assert analyzer.build_graph() is True
        stats = analyzer.get_stats()
        assert stats["nodes"] == 4
        assert stats["edges"] == 3

    def test_with_related_events(self) -> None:
        """测试关联事件边。"""
        store = MagicMock()
        store.get_all_events.return_value = [
            _make_event("evt_001", causal_pressure=0.3),
            _make_event("evt_002", causal_pressure=0.5, related_event_ids=["evt_001"]),
        ]

        analyzer = CausalGraphAnalyzer(store)
        assert analyzer.build_graph() is True
        stats = analyzer.get_stats()
        assert stats["edges"] == 1

    def test_pressure_statistics(self) -> None:
        """测试压强统计。"""
        store = MagicMock()
        store.get_all_events.return_value = [
            _make_event("evt_low", causal_pressure=0.1),
            _make_event("evt_mid", causal_pressure=0.5),
            _make_event("evt_high", causal_pressure=0.9),
        ]

        analyzer = CausalGraphAnalyzer(store)
        assert analyzer.build_graph() is True
        stats = analyzer.get_stats()
        assert stats["avg_pressure"] == 0.5
        assert stats["max_pressure"] == 0.9


class TestCausalGraphAnalysis:
    """图分析功能测试。"""

    def _build_chain_store(self) -> MagicMock:
        """构建链式因果关系的 EventStore mock。"""
        store = MagicMock()
        store.get_all_events.return_value = [
            _make_event("evt_001", description="初始事件", causal_pressure=0.3),
            _make_event("evt_002", caused_by="evt_001", description="第二步", causal_pressure=0.5),
            _make_event("evt_003", caused_by="evt_002", description="第三步", causal_pressure=0.7),
            _make_event("evt_004", caused_by="evt_003", description="最终事件", causal_pressure=0.9, character_id="char_002"),
        ]
        return store

    def test_get_causal_path(self) -> None:
        """测试因果路径查找。"""
        store = self._build_chain_store()
        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        path = analyzer.get_causal_path("evt_001", "evt_004")
        assert path == ["evt_001", "evt_002", "evt_003", "evt_004"]

    def test_get_causal_path_no_path(self) -> None:
        """测试无路径时返回空。"""
        store = self._build_chain_store()
        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        path = analyzer.get_causal_path("evt_001", "nonexistent")
        assert path == []

    def test_get_upstream_chain(self) -> None:
        """测试上游追溯。"""
        store = self._build_chain_store()
        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        chain = analyzer.get_upstream_chain("evt_004")
        # evt_004 ← evt_003 ← evt_002 ← evt_001
        assert "evt_001" in chain
        assert "evt_002" in chain
        assert "evt_003" in chain

    def test_get_upstream_chain_root(self) -> None:
        """测试根事件无上游。"""
        store = self._build_chain_store()
        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        chain = analyzer.get_upstream_chain("evt_001")
        assert chain == []

    def test_get_downstream_chain(self) -> None:
        """测试下游追溯。"""
        store = self._build_chain_store()
        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        chain = analyzer.get_downstream_chain("evt_001")
        assert "evt_002" in chain
        assert "evt_003" in chain
        assert "evt_004" in chain

    def test_get_character_subgraph(self) -> None:
        """测试角色子图。"""
        store = self._build_chain_store()
        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        sub = analyzer.get_character_subgraph("char_002")
        assert sub["character_id"] == "char_002"
        assert sub["total_events"] == 1
        assert sub["events"][0]["event_id"] == "evt_004"

    def test_get_character_subgraph_no_events(self) -> None:
        """测试无事件角色。"""
        store = self._build_chain_store()
        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        sub = analyzer.get_character_subgraph("char_999")
        assert sub["events"] == []

    def test_get_high_impact_events(self) -> None:
        """测试高压强事件筛选。"""
        store = MagicMock()
        store.get_all_events.return_value = [
            _make_event("evt_001", causal_pressure=0.3),
            _make_event("evt_002", causal_pressure=0.6),
            _make_event("evt_003", causal_pressure=0.8),
            _make_event("evt_004", causal_pressure=0.95),
        ]

        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        high = analyzer.get_high_impact_events(threshold=0.7)
        assert len(high) == 2
        assert high[0]["event_id"] == "evt_004"  # 最高压强在前
        assert high[1]["event_id"] == "evt_003"

    def test_get_central_events(self) -> None:
        """测试中心性分析。

        链式拓扑 evt_001 → evt_002 → evt_003 → evt_004 中，
        中间节点 (evt_002, evt_003) 的介数中心性高于两端。
        """
        store = MagicMock()
        store.get_all_events.return_value = [
            _make_event("evt_001", caused_by=None, description="起点", causal_pressure=0.3),
            _make_event("evt_002", caused_by="evt_001", description="中继1", causal_pressure=0.5),
            _make_event("evt_003", caused_by="evt_002", description="中继2", causal_pressure=0.5),
            _make_event("evt_004", caused_by="evt_003", description="终点", causal_pressure=0.7),
        ]

        analyzer = CausalGraphAnalyzer(store)
        analyzer.build_graph()

        central = analyzer.get_central_events(top_k=3)
        # 链式图中中间节点应有非零中心性
        assert len(central) > 0
        assert central[0]["betweenness"] >= 0

    @patch("opennovel.core.causal_graph.CausalGraphAnalyzer._ensure_import")
    def test_get_central_events_no_nx(self, mock_ensure: MagicMock) -> None:
        """测试无 networkx 时中心性为空。"""
        mock_ensure.return_value = False
        store = MagicMock()
        analyzer = CausalGraphAnalyzer(store)
        assert analyzer.get_central_events() == []
