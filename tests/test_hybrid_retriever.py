"""混合检索路由器测试 - Phase 2.3。

测试范围：
- HybridRetriever 初始化
- query_narrative_context 双轨检索
- query_for_writer / query_for_critic 定制策略
- 因果链上下文格式化
- 回退模式（无 EventStore / 无 Retriever）
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opennovel.core.hybrid_retriever import HybridRetriever, RetrievalResult
from opennovel.schemas.event import EventCreate, EventType
from opennovel.storage.sqlite import EventStore


class TestRetrievalResult:
    """RetrievalResult 数据类测试。"""

    def test_default_values(self):
        """默认值正确。"""
        result = RetrievalResult()
        assert result.character_events == []
        assert result.causal_chain == []
        assert result.high_pressure_events == []
        assert result.canon_content == ""
        assert result.subconscious_content == ""
        assert result.causal_chain_context == ""


class TestHybridRetrieverInit:
    """HybridRetriever 初始化测试。"""

    def test_init_with_defaults(self, tmp_path):
        """使用默认参数初始化。"""
        hybrid = HybridRetriever(tmp_path)
        assert hybrid.project_root == tmp_path
        assert hybrid.event_store is None

    def test_init_with_event_store(self, tmp_path):
        """注入 EventStore。"""
        db_path = tmp_path / ".novel.db"
        store = EventStore(db_path)
        hybrid = HybridRetriever(tmp_path, event_store=store)
        assert hybrid.event_store is not None
        store.close()

    def test_init_with_mock_retriever(self, tmp_path):
        """注入 mock Retriever。"""
        mock_ret = MagicMock()
        hybrid = HybridRetriever(tmp_path, retriever=mock_ret)
        assert hybrid.retriever is mock_ret


class TestQueryNarrativeContext:
    """query_narrative_context 双轨检索测试。"""

    def test_sql_path_returns_events(self, tmp_path):
        """SQL 路径返回高压力事件。"""
        db_path = tmp_path / ".novel.db"
        store = EventStore(db_path)

        # 写入测试事件
        store.add_event(EventCreate(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="第1天",
            character_id="char_001",
            event_type=EventType.INJURY,
            description="角色受伤",
            causal_pressure=0.9,
        ))

        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = ""
        mock_ret.query_subconscious.return_value = ""

        hybrid = HybridRetriever(tmp_path, event_store=store, retriever=mock_ret)
        result = hybrid.query_narrative_context("测试查询")

        assert len(result.high_pressure_events) == 1
        assert result.high_pressure_events[0].event_id == "evt_001"
        assert "角色受伤" in result.causal_chain_context
        store.close()

    def test_vector_path_returns_content(self, tmp_path):
        """向量路径返回设定和潜意识内容。"""
        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = "魔法规则：不可杀人"
        mock_ret.query_subconscious.return_value = "灵感：雨夜的灯光"

        hybrid = HybridRetriever(tmp_path, retriever=mock_ret)
        result = hybrid.query_narrative_context("测试查询")

        assert result.canon_content == "魔法规则：不可杀人"
        assert result.subconscious_content == "灵感：雨夜的灯光"

    def test_causal_chain_with_caused_by(self, tmp_path):
        """因果链上下文包含 caused_by 信息。"""
        db_path = tmp_path / ".novel.db"
        store = EventStore(db_path)

        store.add_events_batch([
            EventCreate(
                event_id="evt_001", chapter_id="ch_001", timestamp="t1",
                character_id="char_001", event_type=EventType.INJURY,
                description="受伤", causal_pressure=0.9,
            ),
            EventCreate(
                event_id="evt_002", chapter_id="ch_001", timestamp="t2",
                character_id="char_001", event_type=EventType.HEAL,
                description="治疗", causal_pressure=0.7, caused_by="evt_001",
            ),
        ])

        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = ""
        mock_ret.query_subconscious.return_value = ""

        hybrid = HybridRetriever(tmp_path, event_store=store, retriever=mock_ret)
        result = hybrid.query_narrative_context("测试")

        assert "由 evt_001 引起" in result.causal_chain_context
        store.close()

    def test_no_event_store(self, tmp_path):
        """无 EventStore 时 SQL 路径返回空结果。"""
        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = "设定"
        mock_ret.query_subconscious.return_value = "灵感"

        hybrid = HybridRetriever(tmp_path, retriever=mock_ret)
        result = hybrid.query_narrative_context("测试")

        assert result.high_pressure_events == []
        assert result.causal_chain_context == ""
        assert result.canon_content == "设定"

    def test_character_events_filtered(self, tmp_path):
        """按角色过滤事件。"""
        db_path = tmp_path / ".novel.db"
        store = EventStore(db_path)

        store.add_events_batch([
            EventCreate(
                event_id="evt_001", chapter_id="ch_001", timestamp="t1",
                character_id="char_001", event_type=EventType.INJURY,
                description="角色1受伤", causal_pressure=0.8,
            ),
            EventCreate(
                event_id="evt_002", chapter_id="ch_001", timestamp="t1",
                character_id="char_002", event_type=EventType.INJURY,
                description="角色2受伤", causal_pressure=0.7,
            ),
        ])

        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = ""
        mock_ret.query_subconscious.return_value = ""

        hybrid = HybridRetriever(tmp_path, event_store=store, retriever=mock_ret)
        result = hybrid.query_narrative_context("测试", character_ids=["char_001"])

        char_events = [e for e in result.character_events if e.character_id == "char_001"]
        assert len(char_events) >= 1
        store.close()


class TestWriterCriticStrategies:
    """Writer/Critic 定制检索策略测试。"""

    def test_query_for_writer(self, tmp_path):
        """Writer 策略侧重设定和因果链。"""
        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = "设定内容"
        mock_ret.query_subconscious.return_value = "灵感内容"

        hybrid = HybridRetriever(tmp_path, retriever=mock_ret)
        result = hybrid.query_for_writer("ch_001", "大纲提示")

        assert result.canon_content == "设定内容"
        # Writer 策略使用 top_k_canon=5
        mock_ret.query_canon.assert_called_once()

    def test_query_for_critic(self, tmp_path):
        """Critic 策略侧重因果一致性。"""
        mock_ret = MagicMock()
        mock_ret.query_canon.return_value = "设定内容"
        mock_ret.query_subconscious.return_value = ""

        hybrid = HybridRetriever(tmp_path, retriever=mock_ret)
        result = hybrid.query_for_critic("ch_001", "章节正文")

        assert result.canon_content == "设定内容"


class TestCausalChainFormatting:
    """因果链上下文格式化测试。"""

    def test_empty_events(self, tmp_path):
        """空事件列表返回空字符串。"""
        hybrid = HybridRetriever(tmp_path)
        text = hybrid._build_causal_chain_context([])
        assert text == ""

    def test_related_ids_in_context(self, tmp_path):
        """关联事件 ID 出现在上下文中。"""
        from opennovel.schemas.event import EventLog

        event = EventLog(
            event_id="evt_001",
            chapter_id="ch_001",
            timestamp="t1",
            character_id="char_001",
            event_type="INJURY",
            description="受伤",
            causal_pressure=0.8,
            related_event_ids='["evt_002", "evt_003"]',
        )
        hybrid = HybridRetriever(tmp_path)
        text = hybrid._build_causal_chain_context([event])
        assert "关联: evt_002, evt_003" in text
