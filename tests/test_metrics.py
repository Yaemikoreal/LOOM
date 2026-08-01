"""指标数据库测试 - Phase 2.2。

测试范围：
- MetricsStore 初始化与表创建
- Token 消耗记录与查询
- 评审历史记录与查询
- Agent 轨迹记录与查询
- trace 上下文管理器
"""

import pytest

from opennovel.schemas.metrics import AgentTrace, EvaluationHistory, TokenUsage
from opennovel.storage.metrics import MetricsStore


class TestMetricsStoreInit:
    """MetricsStore 初始化测试。"""

    def test_init_creates_db(self, tmp_path):
        """初始化时创建数据库文件。"""
        db_path = tmp_path / "test.metrics.db"
        store = MetricsStore(db_path)
        assert db_path.exists()
        store.close()

    def test_context_manager(self, tmp_path):
        """上下文管理器正确关闭。"""
        db_path = tmp_path / "test.metrics.db"
        with MetricsStore(db_path) as store:
            assert db_path.exists()


class TestTokenUsage:
    """Token 消耗记录测试。"""

    def test_record_and_query(self, tmp_path):
        """记录并查询 token 消耗。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_token_usage(
                agent="writer",
                chapter_id="ch_001",
                model="gpt-4",
                prompt_tokens=100,
                completion_tokens=200,
            )
            usage = store.get_total_usage()
            assert usage["prompt_tokens"] == 100
            assert usage["completion_tokens"] == 200
            assert usage["total_tokens"] == 300

    def test_multiple_records(self, tmp_path):
        """多条记录正确累加。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_token_usage("writer", "ch_001", "gpt-4", 100, 200)
            store.record_token_usage("writer", "ch_001", "gpt-4", 50, 100)
            store.record_token_usage("critic", "ch_001", "gpt-4", 80, 150)

            total = store.get_total_usage()
            assert total["prompt_tokens"] == 230
            assert total["completion_tokens"] == 450
            assert total["total_tokens"] == 680

    def test_filter_by_agent(self, tmp_path):
        """按 Agent 过滤。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_token_usage("writer", "ch_001", "gpt-4", 100, 200)
            store.record_token_usage("critic", "ch_001", "gpt-4", 80, 150)

            writer_usage = store.get_total_usage(agent="writer")
            assert writer_usage["total_tokens"] == 300

            critic_usage = store.get_total_usage(agent="critic")
            assert critic_usage["total_tokens"] == 230

    def test_filter_by_chapter(self, tmp_path):
        """按章节过滤。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_token_usage("writer", "ch_001", "gpt-4", 100, 200)
            store.record_token_usage("writer", "ch_002", "gpt-4", 50, 100)

            ch1 = store.get_total_usage(chapter_id="ch_001")
            assert ch1["total_tokens"] == 300

    def test_usage_by_agent(self, tmp_path):
        """按 Agent 分组汇总。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_token_usage("writer", "ch_001", "gpt-4", 100, 200)
            store.record_token_usage("writer", "ch_001", "gpt-4", 50, 100)
            store.record_token_usage("critic", "ch_001", "gpt-4", 80, 150)

            by_agent = store.get_usage_by_agent()
            # writer: (100+200) + (50+100) = 300 + 150 = 450
            assert by_agent["writer"]["total_tokens"] == 450
            assert by_agent["critic"]["total_tokens"] == 230

    def test_call_type_recorded(self, tmp_path):
        """调用类型正确记录。"""
        with MetricsStore(tmp_path / "test.db") as store:
            record = store.record_token_usage(
                "writer", "ch_001", "gpt-4", 100, 200, call_type="write"
            )
            assert record.call_type == "write"


class TestEvaluationHistory:
    """评审历史记录测试。"""

    def test_record_and_query(self, tmp_path):
        """记录并查询评审历史。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_evaluation(
                chapter_id="ch_001",
                total_score=85,
                dimensions=[18, 17, 17, 16, 17],
                is_pass=True,
            )
            history = store.get_evaluation_history()
            assert len(history) == 1
            assert history[0].total_score == 85
            assert history[0].is_pass is True

    def test_dimensions_stored(self, tmp_path):
        """五维分数正确存储。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_evaluation(
                chapter_id="ch_001",
                total_score=80,
                dimensions=[16, 16, 16, 16, 16],
                is_pass=True,
            )
            history = store.get_evaluation_history()
            assert history[0].dimension_writing == 16
            assert history[0].dimension_plot == 16
            assert history[0].dimension_character == 16
            assert history[0].dimension_rhythm == 16
            assert history[0].dimension_emotion == 16

    def test_filter_by_chapter(self, tmp_path):
        """按章节过滤评审历史。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_evaluation("ch_001", 85, [18, 17, 17, 16, 17], True)
            store.record_evaluation("ch_002", 70, [14, 14, 14, 14, 14], False)

            ch1 = store.get_evaluation_history(chapter_id="ch_001")
            assert len(ch1) == 1
            assert ch1[0].total_score == 85

    def test_average_scores(self, tmp_path):
        """平均分计算正确。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_evaluation("ch_001", 80, [16, 16, 16, 16, 16], True)
            store.record_evaluation("ch_002", 90, [18, 18, 18, 18, 18], True)

            avg = store.get_average_scores()
            assert avg["total"] == 85.0
            assert avg["writing"] == 17.0

    def test_average_scores_empty(self, tmp_path):
        """无记录时返回空字典。"""
        with MetricsStore(tmp_path / "test.db") as store:
            avg = store.get_average_scores()
            assert avg == {}

    def test_retry_count(self, tmp_path):
        """重试次数正确记录。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_evaluation("ch_001", 75, [15, 15, 15, 15, 15], False, retry_count=2)
            history = store.get_evaluation_history()
            assert history[0].retry_count == 2

    def test_mode_recorded(self, tmp_path):
        """评审模式正确记录。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_evaluation("ch_001", 48, [17, 16, 15], True, mode="outline")
            history = store.get_evaluation_history()
            assert history[0].mode == "outline"


class TestAgentTrace:
    """Agent 轨迹记录测试。"""

    def test_record_and_query(self, tmp_path):
        """记录并查询 Agent 轨迹。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_trace(
                agent="writer",
                action="write",
                chapter_id="ch_001",
                duration_ms=1500,
            )
            traces = store.get_traces()
            assert len(traces) == 1
            assert traces[0].agent == "writer"
            assert traces[0].action == "write"
            assert traces[0].duration_ms == 1500

    def test_filter_by_agent(self, tmp_path):
        """按 Agent 过滤轨迹。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_trace("writer", "write", "ch_001")
            store.record_trace("critic", "evaluate", "ch_001")

            writer_traces = store.get_traces(agent="writer")
            assert len(writer_traces) == 1
            assert writer_traces[0].agent == "writer"

    def test_filter_by_chapter(self, tmp_path):
        """按章节过滤轨迹。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_trace("writer", "write", "ch_001")
            store.record_trace("writer", "write", "ch_002")

            ch1 = store.get_traces(chapter_id="ch_001")
            assert len(ch1) == 1

    def test_error_status(self, tmp_path):
        """错误状态正确记录。"""
        with MetricsStore(tmp_path / "test.db") as store:
            store.record_trace(
                "manager", "update", "ch_001",
                status="error", detail="JSON 解析失败",
            )
            traces = store.get_traces()
            assert traces[0].status == "error"
            assert traces[0].detail == "JSON 解析失败"

    def test_limit(self, tmp_path):
        """limit 参数限制返回数量。"""
        with MetricsStore(tmp_path / "test.db") as store:
            for i in range(10):
                store.record_trace("writer", "write", f"ch_{i:03d}")

            traces = store.get_traces(limit=3)
            assert len(traces) == 3


class TestTraceContextManager:
    """trace 上下文管理器测试。"""

    def test_success_trace(self, tmp_path):
        """成功执行记录 success 状态。"""
        with MetricsStore(tmp_path / "test.db") as store:
            with store.trace("writer", "write", "ch_001"):
                pass  # 模拟成功执行

            traces = store.get_traces()
            assert len(traces) == 1
            assert traces[0].status == "success"
            assert traces[0].duration_ms >= 0

    def test_error_trace(self, tmp_path):
        """异常执行记录 error 状态。"""
        with MetricsStore(tmp_path / "test.db") as store:
            with pytest.raises(ValueError):
                with store.trace("writer", "write", "ch_001"):
                    raise ValueError("测试错误")

            traces = store.get_traces()
            assert len(traces) == 1
            assert traces[0].status == "error"
            assert "测试错误" in traces[0].detail

    def test_duration_measured(self, tmp_path):
        """耗时测量合理。"""
        import time

        with MetricsStore(tmp_path / "test.db") as store:
            with store.trace("writer", "write", "ch_001"):
                time.sleep(0.01)  # 10ms

            traces = store.get_traces()
            assert traces[0].duration_ms >= 5  # 至少 5ms

    def test_no_agent_name_skips(self, tmp_path):
        """不指定 agent_name 时不报错（兼容无 MetricsStore 场景）。"""
        # 这个测试验证 LLMBus 在无 metrics_store 时不报错
        from opennovel.core.llm import LLMBus

        bus = LLMBus(model="gpt-4")  # 无 metrics_store
        # 不应抛出异常
        assert bus.metrics_store is None
