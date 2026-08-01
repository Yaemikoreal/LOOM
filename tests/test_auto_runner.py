"""AutoRunner 模块测试 - 三 Agent 自主创作编排器。"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from opennovel.agents.critic import Critic
from opennovel.agents.manager import Manager
from opennovel.agents.writer import Writer
from opennovel.core.auto_runner import AutoRunner, ChapterResult
from opennovel.core.config import LoomConfig
from opennovel.schemas.evaluation import ChapterEvaluation, DimensionScore
from opennovel.schemas.manager_update import ManagerUpdateResult
from opennovel.schemas.outline import ChapterOutline, SceneBreakdown

# ── Mock LiteLLM 响应对象（属性访问模式）──


class MockMessage:
    def __init__(self, text: str) -> None:
        self.content = text


class MockChoice:
    def __init__(self, text: str) -> None:
        self.message = MockMessage(text)


class MockUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 10
        self.total_tokens = 20


class MockLLMResponse:
    """模拟 LiteLLM 响应对象（属性访问，非字典）。"""

    def __init__(self, text: str) -> None:
        self.choices = [MockChoice(text)]
        self.usage = MockUsage()


class MockLLMBus:
    """模拟 LLMBus，可控的响应序列。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = [MockLLMResponse(r) for r in responses]
        self.call_count = 0
        self.last_messages: list[dict[str, str]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        **kwargs: Any,
    ) -> MockLLMResponse:
        response = self.responses[self.call_count]
        self.call_count += 1
        self.last_messages = messages
        return response


# ── Fixtures ──


def make_outline(chapter_id: str = "ch_001") -> ChapterOutline:
    """构造测试用 ChapterOutline。"""
    return ChapterOutline(
        chapter_id=chapter_id,
        title="第一章：相遇",
        summary="四人在加油站相遇，气氛紧张",
        scenes=[
            SceneBreakdown(
                scene_id="scene_1",
                description="加油站场景",
                characters_involved=["char_001"],
                emotional_tone="紧张",
                estimated_words=800,
            )
        ],
        character_arcs={"char_001": "从怀疑到信任"},
        key_plot_points=["四人首次相遇"],
        narrative_rhythm="快节奏",
        target_words=3000,
    )


def make_evaluation(total_score: int = 85) -> ChapterEvaluation:
    """构造测试用 ChapterEvaluation。"""
    return ChapterEvaluation(
        total_score=total_score,
        dimensions=[
            DimensionScore(dimension="文笔质量", score=18, comment="优秀"),
            DimensionScore(dimension="情节逻辑", score=17, comment="合理"),
            DimensionScore(dimension="角色一致性", score=17, comment="一致"),
            DimensionScore(dimension="节奏把控", score=16, comment="紧凑"),
            DimensionScore(dimension="情感表达", score=17, comment="细腻"),
        ],
        summary="整体质量不错",
        issues=[],
        suggestions=[],
    )


def make_manager_result() -> ManagerUpdateResult:
    """构造测试用 ManagerUpdateResult。"""
    return ManagerUpdateResult(
        character_updates=[],
        events=[],
        chapter_summary="第一章摘要：四人相遇",
    )


@pytest.fixture
def empty_project_root(tmp_path: Path) -> Path:
    """临时项目根目录，包含标准目录结构。"""
    root = tmp_path / "test_project"
    root.mkdir()
    (root / "draft").mkdir()
    (root / "characters").mkdir()
    (root / "canon").mkdir()
    (root / "outlines").mkdir()
    (root / "prompts").mkdir()
    (root / ".snapshots").mkdir()
    (root / ".index").mkdir()
    # 写入 novel.yaml 配置
    (root / "novel.yaml").write_text(
        "model: test-model\napi_base: http://localhost:8080\napi_key: test-key\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def default_config() -> LoomConfig:
    """默认测试配置。

    默认禁用安全围栏，使 AutoRunner 使用传统 write() 路径。
    """
    from opennovel.core.safety_fence import SafetyFenceConfig

    return LoomConfig(
        model="test-model",
        api_base="http://localhost:8080",
        api_key="test-key",
        creative_direction="黑暗奇幻风格",
        target_chapters=5,
        words_per_chapter=3000,
        safety_fence=SafetyFenceConfig(enabled=False),
    )


@pytest.fixture
def outline_text() -> str:
    """测试大纲文本。"""
    return """## 第一章：相遇

四人在加油站相遇，气氛紧张。

## 第二章：冲突

矛盾爆发，战斗开始。
"""


# ── 初始化测试 ──


class TestAutoRunnerInit:
    """AutoRunner 初始化测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    @patch("opennovel.core.auto_runner.Writer")
    @patch("opennovel.core.auto_runner.Critic")
    @patch("opennovel.core.auto_runner.Manager")
    def test_init_creates_three_agents(
        self,
        mock_manager_cls: MagicMock,
        mock_critic_cls: MagicMock,
        mock_writer_cls: MagicMock,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试初始化创建三个 Agent。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        assert runner.writer is not None
        assert runner.critic is not None
        assert runner.manager is not None
        # 验证三个 Agent 被创建
        mock_writer_cls.assert_called_once()
        mock_critic_cls.assert_called_once()
        mock_manager_cls.assert_called_once()

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    @patch("opennovel.core.auto_runner.Writer")
    @patch("opennovel.core.auto_runner.Critic")
    @patch("opennovel.core.auto_runner.Manager")
    def test_init_llm_bus_configs(
        self,
        mock_manager_cls: MagicMock,
        mock_critic_cls: MagicMock,
        mock_writer_cls: MagicMock,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试四个 Agent 的 LLMBus 配置正确传递。"""
        AutoRunner(project_root=empty_project_root, config=default_config)

        # 验证 LLMBus 被创建 4 次（writer, critic, manager, director）
        assert mock_llm_bus_cls.call_count == 4

        # 验证每次调用都传递了正确的配置
        for call in mock_llm_bus_cls.call_args_list:
            kwargs = call[1]
            assert kwargs["model"] == "test-model"
            assert kwargs["api_base"] == "http://localhost:8080"
            assert kwargs["api_key"] == "test-key"

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    @patch("opennovel.core.auto_runner.Writer")
    @patch("opennovel.core.auto_runner.Critic")
    @patch("opennovel.core.auto_runner.Manager")
    def test_init_with_agent_specific_config(
        self,
        mock_manager_cls: MagicMock,
        mock_critic_cls: MagicMock,
        mock_writer_cls: MagicMock,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
    ) -> None:
        """测试 per-agent LLM 配置覆盖。"""
        from opennovel.core.config import AgentConfig

        config = LoomConfig(
            model="default-model",
            api_base="http://default:8080",
            api_key="default-key",
            agent_writer=AgentConfig(model="writer-model"),
            agent_critic=AgentConfig(model="critic-model"),
            agent_manager=AgentConfig(model="manager-model"),
        )

        AutoRunner(project_root=empty_project_root, config=config)

        # 验证每个 Agent 使用了自己的模型
        calls = mock_llm_bus_cls.call_args_list
        assert calls[0][1]["model"] == "writer-model"
        assert calls[1][1]["model"] == "critic-model"
        assert calls[2][1]["model"] == "manager-model"


# ── 大纲解析测试 ──


class TestParseOutline:
    """_parse_outline 大纲解析测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    @patch("opennovel.core.auto_runner.Writer")
    @patch("opennovel.core.auto_runner.Critic")
    @patch("opennovel.core.auto_runner.Manager")
    def test_parse_two_chapters(
        self,
        mock_manager_cls: MagicMock,
        mock_critic_cls: MagicMock,
        mock_writer_cls: MagicMock,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
        outline_text: str,
    ) -> None:
        """测试解析两章大纲。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        chapters = runner._parse_outline(outline_text)

        assert len(chapters) == 2
        assert chapters[0][0] == "ch_001"
        assert "相遇" in chapters[0][1]
        assert chapters[1][0] == "ch_002"
        assert "冲突" in chapters[1][1]

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    @patch("opennovel.core.auto_runner.Writer")
    @patch("opennovel.core.auto_runner.Critic")
    @patch("opennovel.core.auto_runner.Manager")
    def test_parse_empty_outline(
        self,
        mock_manager_cls: MagicMock,
        mock_critic_cls: MagicMock,
        mock_writer_cls: MagicMock,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试空大纲返回空列表。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        chapters = runner._parse_outline("")
        assert chapters == []

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    @patch("opennovel.core.auto_runner.Writer")
    @patch("opennovel.core.auto_runner.Critic")
    @patch("opennovel.core.auto_runner.Manager")
    def test_parse_single_chapter(
        self,
        mock_manager_cls: MagicMock,
        mock_critic_cls: MagicMock,
        mock_writer_cls: MagicMock,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试单章大纲解析。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        text = "## 第一章：开篇\n\n故事开始。"
        chapters = runner._parse_outline(text)

        assert len(chapters) == 1
        assert chapters[0][0] == "ch_001"
        assert "开篇" in chapters[0][1]

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    @patch("opennovel.core.auto_runner.Writer")
    @patch("opennovel.core.auto_runner.Critic")
    @patch("opennovel.core.auto_runner.Manager")
    def test_parse_preserves_body_text(
        self,
        mock_manager_cls: MagicMock,
        mock_critic_cls: MagicMock,
        mock_writer_cls: MagicMock,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试大纲解析保留正文内容。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        text = "## 第一章：开篇\n\n这是第一章的详细描述。\n包含多行内容。"
        chapters = runner._parse_outline(text)

        assert "详细描述" in chapters[0][1]
        assert "多行内容" in chapters[0][1]


# ── 单章流程测试 ──


class TestRunChapter:
    """run_chapter 单章创作流程测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_run_chapter_success(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试单章完整创作流程：think → write → evaluate → update。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        # Mock Writer
        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章：相遇\n\n这是正文内容。"

        # Mock Critic
        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(85)

        # Mock Manager
        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        result = runner.run_chapter("ch_001", "四人在加油站相遇")

        # 验证 Writer 被调用
        runner.writer.think.assert_called_once()
        runner.writer.write.assert_called_once()

        # 验证 Critic 被调用
        runner.critic.evaluate.assert_called_once()

        # 验证 Manager 被调用
        runner.manager.update.assert_called_once()

        # 验证返回结果
        assert isinstance(result, ChapterResult)
        assert result.chapter_id == "ch_001"
        assert result.evaluation.total_score == 85
        assert result.retry_count == 0
        assert result.manager_summary == "第一章摘要：四人相遇"

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_run_chapter_writes_file(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 run_chapter 将章节写入文件。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章：相遇\n\n正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(85)

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter("ch_001", "四人相遇")

        # 验证章节文件已写入
        chapter_path = empty_project_root / "draft" / "ch_001.md"
        assert chapter_path.exists()

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_run_chapter_with_previous_context(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 run_chapter 传递前文上下文。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第二章\n\n正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(85)

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter(
            "ch_002",
            "冲突爆发",
            previous_summary="前一章摘要",
            previous_text="前一章正文末尾",
        )

        # 验证 Writer.think 收到前文摘要
        think_call = runner.writer.think.call_args
        assert think_call[0][2] == "前一章摘要"  # previous_summary

        # 验证 Writer.write 收到前文正文
        write_call = runner.writer.write.call_args
        assert write_call[0][2] == "前一章正文末尾"  # previous_chapter_text

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_run_chapter_manager_failure_graceful(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 Manager 失败时优雅降级（不中断流程）。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(85)

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.side_effect = RuntimeError("Manager 失败")

        result = runner.run_chapter("ch_001", "四人相遇")

        # Manager 失败不应中断流程，摘要为空
        assert result.manager_summary == ""
        assert result.evaluation.total_score == 85


# ── 不合格重试测试 ──


class TestRunChapterRetry:
    """run_chapter 不合格重试流程测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_retry_then_pass(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试第一次不合格（75分），修订后合格（85分）。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n初稿正文。"
        runner.writer.revise.return_value = "# 第一章\n\n修订后正文。"

        runner.critic = MagicMock(spec=Critic)
        # 第一次 75 分（不合格），第二次 85 分（合格）
        runner.critic.evaluate.side_effect = [
            make_evaluation(75),
            make_evaluation(85),
        ]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        result = runner.run_chapter("ch_001", "四人相遇")

        assert result.evaluation.total_score == 85
        assert result.retry_count == 1
        # Writer.revise 被调用一次
        runner.writer.revise.assert_called_once()
        # Critic.evaluate 被调用两次
        assert runner.critic.evaluate.call_count == 2

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_retry_passes_feedback_to_writer(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试重试时将 Critic 反馈传递给 Writer.revise。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n初稿。"
        runner.writer.revise.return_value = "# 第一章\n\n修订稿。"

        # 构造带 issues 和 suggestions 的评价
        bad_eval = ChapterEvaluation(
            total_score=75,
            dimensions=[
                DimensionScore(dimension="文笔质量", score=15, comment="一般"),
                DimensionScore(dimension="情节逻辑", score=15, comment="一般"),
                DimensionScore(dimension="角色一致性", score=15, comment="一般"),
                DimensionScore(dimension="节奏把控", score=15, comment="一般"),
                DimensionScore(dimension="情感表达", score=15, comment="一般"),
            ],
            summary="需要改进",
            issues=["文笔平淡", "节奏拖沓"],
            suggestions=["增加细节描写", "加快节奏"],
        )

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.side_effect = [bad_eval, make_evaluation(85)]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter("ch_001", "四人相遇")

        # 验证 revise 收到反馈
        revise_call = runner.writer.revise.call_args
        feedback = revise_call[0][3]  # feedback 参数
        assert "文笔平淡" in feedback
        assert "增加细节描写" in feedback


# ── 最大重试次数测试 ──


class TestRunChapterMaxRetries:
    """run_chapter 达到最大重试次数后取最高分版本。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_max_retries_takes_best_score(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 5 次重试后取最高分版本。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n初稿。"
        runner.writer.revise.return_value = "# 第一章\n\n修订稿。"

        # 6 次评分（1 次初始 + 5 次重试），分数递增但始终不合格
        scores = [70, 72, 74, 76, 78, 79]
        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.side_effect = [make_evaluation(s) for s in scores]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        result = runner.run_chapter("ch_001", "四人相遇")

        # 应该取最高分版本（79 分）
        assert result.evaluation.total_score == 79
        # 重试次数为 5（最后一次 attempt）
        assert result.retry_count == 5
        # Writer.revise 被调用 5 次
        assert runner.writer.revise.call_count == 5
        # Critic.evaluate 被调用 6 次
        assert runner.critic.evaluate.call_count == 6

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_max_retries_writes_file(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试达到最大重试次数后仍写入章节文件。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n初稿。"
        runner.writer.revise.return_value = "# 第一章\n\n修订稿。"

        # 所有评分都不合格
        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.side_effect = [make_evaluation(70) for _ in range(6)]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter("ch_001", "四人相遇")

        # 验证章节文件仍然写入
        chapter_path = empty_project_root / "draft" / "ch_001.md"
        assert chapter_path.exists()

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_early_pass_skips_remaining_retries(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试提前合格后跳过剩余重试。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n初稿。"
        runner.writer.revise.return_value = "# 第一章\n\n修订稿。"

        # 第一次不合格，第二次合格
        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.side_effect = [make_evaluation(75), make_evaluation(85)]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        result = runner.run_chapter("ch_001", "四人相遇")

        assert result.retry_count == 1
        # Writer.revise 只调用 1 次
        assert runner.writer.revise.call_count == 1
        # Critic.evaluate 只调用 2 次
        assert runner.critic.evaluate.call_count == 2


# ── 辅助方法测试 ──


class TestAutoRunnerHelpers:
    """AutoRunner 辅助方法测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_get_active_characters_empty(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试无角色文件时返回空列表。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chars = runner._get_active_characters()
        assert chars == []

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_get_active_characters_with_files(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试有角色文件时返回排序后的 ID 列表。"""
        # 创建角色文件
        chars_dir = empty_project_root / "characters"
        (chars_dir / "char_002.md").write_text("---\nid: char_002\n---\n正文", encoding="utf-8")
        (chars_dir / "char_001.md").write_text("---\nid: char_001\n---\n正文", encoding="utf-8")

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chars = runner._get_active_characters()

        assert chars == ["char_001", "char_002"]

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_get_previous_summary_first_chapter(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试第一章无前文摘要。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        summary = runner._get_previous_summary(0, [])
        assert summary == ""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_get_previous_summary_with_results(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试从前一章结果获取摘要。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        prev_result = ChapterResult(
            chapter_id="ch_001",
            outline=make_outline(),
            chapter_text="正文",
            evaluation=make_evaluation(),
            retry_count=0,
            manager_summary="前一章摘要",
            word_count=100,
        )

        summary = runner._get_previous_summary(1, [prev_result])
        assert summary == "前一章摘要"

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_get_previous_chapter_text_truncates(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试前文正文超过 2000 字时截断。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        long_text = "字" * 3000
        prev_result = ChapterResult(
            chapter_id="ch_001",
            outline=make_outline(),
            chapter_text=long_text,
            evaluation=make_evaluation(),
            retry_count=0,
            manager_summary="摘要",
            word_count=3000,
        )

        text = runner._get_previous_chapter_text(1, [prev_result])
        assert len(text) == 2000


# ── 章节类型检测测试 ──


class TestDetectChapterType:
    """detect_chapter_type 章节类型检测测试。"""

    def test_detect_climax(self) -> None:
        """测试高潮关键词触发 CLIMAX 类型。"""
        from opennovel.core.chapter_utils import ChapterType, detect_chapter_type

        for hint in [
            "故事来到转折点",
            "高潮：最终对决",
            "climax 大战",
            "决战时刻",
            "大结局篇",
        ]:
            assert detect_chapter_type(hint) == ChapterType.CLIMAX, f"未检测到高潮: {hint}"

    def test_detect_transition(self) -> None:
        """测试过渡关键词触发 TRANSITION 类型。"""
        from opennovel.core.chapter_utils import ChapterType, detect_chapter_type

        for hint in [
            "过渡章节",
            "日常篇：平静的生活",
            "transition",
            "休整与准备",
        ]:
            assert detect_chapter_type(hint) == ChapterType.TRANSITION, f"未检测到过渡: {hint}"

    def test_detect_routine(self) -> None:
        """测试无特殊关键词时返回 ROUTINE 类型。"""
        from opennovel.core.chapter_utils import ChapterType, detect_chapter_type

        for hint in [
            "普通的冒险",
            "探索地下城",
            "新的任务",
            "继续旅程",
        ]:
            assert detect_chapter_type(hint) == ChapterType.ROUTINE, f"误检测: {hint}"

    def test_case_insensitive(self) -> None:
        """测试关键词匹配不区分大小写。"""
        from opennovel.core.chapter_utils import ChapterType, detect_chapter_type

        assert detect_chapter_type("CLIMAX") == ChapterType.CLIMAX
        assert detect_chapter_type("Transition") == ChapterType.TRANSITION


# ── 条件跳转判断测试 ──


class TestShouldSkipManager:
    """should_skip_manager 条件跳转判断测试。"""

    def test_skip_when_score_high(self) -> None:
        """测试评分 >= 90 时跳过 Manager。"""
        from opennovel.core.auto_runner import should_skip_manager

        high_score = make_evaluation(92)
        assert should_skip_manager(high_score) is True

        # 边界值
        edge_score = make_evaluation(90)
        assert should_skip_manager(edge_score) is True

    def test_not_skip_when_score_low(self) -> None:
        """测试评分 < 90 时不跳过 Manager。"""
        from opennovel.core.auto_runner import should_skip_manager

        low_score = make_evaluation(85)
        assert should_skip_manager(low_score) is False

    def test_not_skip_when_anchored_issues(self) -> None:
        """测试有锚定问题时即使高分也不跳过。"""
        from opennovel.core.auto_runner import should_skip_manager
        from opennovel.schemas.evaluation import AnchoredIssue, DimensionScore

        eval_with_issues = ChapterEvaluation(
            total_score=92,
            dimensions=[DimensionScore(dimension="文笔质量", score=18, comment="")]
            + [DimensionScore(dimension=f"dim{i}", score=18, comment="") for i in range(4)],
            summary="有小问题",
            issues=[],
            suggestions=[],
            anchored_issues=[
                AnchoredIssue(
                    dimension="情节",
                    severity="minor",
                    quote="这是一段长度超过二十个汉字的引用文本用于测试定位功能",
                    problem="小问题",
                    suggestion="改进建议",
                )
            ],
        )
        assert should_skip_manager(eval_with_issues) is False


class TestShouldSkipDirector:
    """should_skip_director 条件路由测试。"""

    def test_skip_last_chapter(self) -> None:
        """测试最后一章跳过 Director。"""
        from opennovel.core.auto_runner import should_skip_director
        from opennovel.core.chapter_utils import ChapterType

        assert should_skip_director(ChapterType.CLIMAX, 4, 5) is True

    def test_run_climax(self) -> None:
        """测试高潮章节强制运行 Director。"""
        from opennovel.core.auto_runner import should_skip_director
        from opennovel.core.chapter_utils import ChapterType

        assert should_skip_director(ChapterType.CLIMAX, 0, 3) is False

    def test_skip_transition(self) -> None:
        """测试过渡章节跳过 Director。"""
        from opennovel.core.auto_runner import should_skip_director
        from opennovel.core.chapter_utils import ChapterType

        assert should_skip_director(ChapterType.TRANSITION, 0, 3) is True

    def test_routine_every_n_chapters(self) -> None:
        """测试日常章节每 N 章运行一次 Director。"""
        from opennovel.core.auto_runner import DIRECTOR_INTERVAL, should_skip_director
        from opennovel.core.chapter_utils import ChapterType

        # 第 0 章（首个日常章节）→ 运行
        assert should_skip_director(ChapterType.ROUTINE, 0, 5) is False

        # 第 1 章 → 跳过
        assert should_skip_director(ChapterType.ROUTINE, 1, 5) is True

        # 第 DIRECTOR_INTERVAL 章 → 运行
        assert should_skip_director(ChapterType.ROUTINE, DIRECTOR_INTERVAL, 5) is False


# ── 条件管线集成测试 ──


class TestConditionalPipeline:
    """条件化管线集成测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_skip_manager_when_excellent(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试评分 >= 90 时跳过 Manager 更新。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n优秀正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(92)  # 高分

        runner.manager = MagicMock(spec=Manager)

        result = runner.run_chapter("ch_001", "四人在加油站相遇")

        # Manager.update 不应被调用
        runner.manager.update.assert_not_called()
        # manager_skipped 标志为 True
        assert result.manager_skipped is True
        # manager_summary 为空（延后批处理）
        assert result.manager_summary == ""
        # 其余字段正常
        assert result.chapter_id == "ch_001"
        assert result.evaluation.total_score == 92
        assert result.retry_count == 0

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_run_manager_when_score_moderate(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试评分 < 90 时正常运行 Manager。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n普通正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(85)  # 合格但不高分

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        result = runner.run_chapter("ch_001", "四人在加油站相遇")

        # Manager.update 应被调用
        runner.manager.update.assert_called_once()
        # manager_skipped 为 False
        assert result.manager_skipped is False
        # manager_summary 正常
        assert result.manager_summary == "第一章摘要：四人相遇"

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_batch_process_deferred_updates(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试批处理回填延后的 Manager 更新。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(92)

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        # 模拟积累延后数据
        from opennovel.core.auto_runner import DeferredManagerData
        from opennovel.core.chapter_utils import ChapterType

        runner._deferred_manager_updates.append(
            DeferredManagerData(
                chapter_id="ch_001",
                chapter_text="正文",
                active_characters=["char_001"],
                chapter_type=ChapterType.ROUTINE,
            )
        )

        # 模拟一个延后的 ChapterResult
        result = ChapterResult(
            chapter_id="ch_001",
            outline=make_outline(),
            chapter_text="正文",
            evaluation=make_evaluation(92),
            retry_count=0,
            manager_skipped=True,
        )

        runner._process_deferred_manager_updates([result])

        # Manager.update 被批处理调用
        runner.manager.update.assert_called_once()
        # ChapterResult 被回填
        assert result.manager_skipped is False
        assert result.manager_summary == "第一章摘要：四人相遇"
        # 队列已清空
        assert len(runner._deferred_manager_updates) == 0

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_no_chapter_result_found_in_batch(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试批处理时找不到对应 ChapterResult 不崩溃。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        from opennovel.core.auto_runner import DeferredManagerData
        from opennovel.core.chapter_utils import ChapterType

        runner._deferred_manager_updates.append(
            DeferredManagerData(
                chapter_id="ch_999",
                chapter_text="正文",
                active_characters=[],
                chapter_type=ChapterType.ROUTINE,
            )
        )

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        # 不应崩溃
        runner._process_deferred_manager_updates([])

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_batch_manager_failure_graceful(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试批处理中 Manager 失败不中断整体流程。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        from opennovel.core.auto_runner import DeferredManagerData
        from opennovel.core.chapter_utils import ChapterType

        runner._deferred_manager_updates.append(
            DeferredManagerData(
                chapter_id="ch_001",
                chapter_text="正文",
                active_characters=["char_001"],
                chapter_type=ChapterType.ROUTINE,
            )
        )

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.side_effect = RuntimeError("批量 Manager 失败")

        result = ChapterResult(
            chapter_id="ch_001",
            outline=make_outline(),
            chapter_text="正文",
            evaluation=make_evaluation(92),
            retry_count=0,
            manager_skipped=True,
        )

        # 不应抛出异常
        runner._process_deferred_manager_updates([result])
        # 失败时 ChapterResult 保持原状
        assert result.manager_skipped is True


# ── 独立函数 parse_outline_from_text 测试 ──


class TestParseOutlineFromText:
    """parse_outline_from_text 独立函数测试。"""

    def test_parse_two_chapters(self) -> None:
        """测试解析两章大纲。"""
        from opennovel.core.auto_runner import parse_outline_from_text

        text = """## 第一章：相遇

四人在加油站相遇。

## 第二章：冲突

矛盾爆发。
"""
        chapters = parse_outline_from_text(text)
        assert len(chapters) == 2
        assert chapters[0][0] == "ch_001"
        assert "相遇" in chapters[0][1]
        assert chapters[1][0] == "ch_002"
        assert "冲突" in chapters[1][1]

    def test_parse_empty(self) -> None:
        """测试空大纲返回空列表。"""
        from opennovel.core.auto_runner import parse_outline_from_text

        assert parse_outline_from_text("") == []
        assert parse_outline_from_text("   ") == []

    def test_parse_no_headings(self) -> None:
        """测试无 ## 标题的大纲返回空列表。"""
        from opennovel.core.auto_runner import parse_outline_from_text

        assert parse_outline_from_text("只有普通文本\n没有标题") == []


# ── AutoRunner 热修复集成测试 ──


class TestAutoRunnerHotFix:
    """AutoRunner 局部热修复集成测试。"""

    def _make_evaluation_with_anchored_issues(self, score: int = 75) -> ChapterEvaluation:
        """构造带锚定问题的评审结果。"""
        from opennovel.schemas.evaluation import AnchoredIssue, DimensionScore

        return ChapterEvaluation(
            total_score=score,
            dimensions=[
                DimensionScore(dimension="文笔质量", score=15, comment="需改进"),
                DimensionScore(dimension="情节逻辑", score=15, comment="需改进"),
                DimensionScore(dimension="角色一致性", score=15, comment="需改进"),
                DimensionScore(dimension="节奏把控", score=15, comment="需改进"),
                DimensionScore(dimension="情感表达", score=15, comment="需改进"),
            ],
            summary="需要改进",
            issues=[],
            suggestions=[],
            anchored_issues=[
                AnchoredIssue(
                    dimension="角色一致性",
                    severity="major",
                    quote="这是一段超过二十个汉字的引用文本用于定位问题段落",
                    problem="角色行为不一致",
                    suggestion="调整角色反应",
                ),
            ],
        )

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_uses_hot_fix_when_anchored_issues_present(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试有锚定问题时 AutoRunner 调用 hot_fix 而非 revise。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n正文内容包含" + "这段文本" * 20
        runner.writer.hot_fix.return_value = "# 第一章\n\n修复后的正文。"
        runner.writer.revise.return_value = "# 第一章\n\n修订后的正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.side_effect = [
            self._make_evaluation_with_anchored_issues(75),  # 不合格，有锚定问题
            make_evaluation(85),  # 第二次合格
        ]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter("ch_001", "四人相遇")

        # hot_fix 被调用（优先于 revise）
        runner.writer.hot_fix.assert_called_once()
        # revise 不应被调用（hot_fix 成功）
        runner.writer.revise.assert_not_called()

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_falls_back_to_revise_when_hot_fix_fails(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 hot_fix 返回 None 时回退到 revise。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n正文内容包含" + "这段文本" * 20
        runner.writer.hot_fix.return_value = None  # hot_fix 失败
        runner.writer.revise.return_value = "# 第一章\n\n修订后的正文。"

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.side_effect = [
            self._make_evaluation_with_anchored_issues(75),  # 不合格
            make_evaluation(85),  # 第二次合格
        ]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter("ch_001", "四人相遇")

        # hot_fix 被调用但失败
        runner.writer.hot_fix.assert_called_once()
        # 回退到 revise
        runner.writer.revise.assert_called_once()

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_uses_revise_when_no_anchored_issues(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试无锚定问题时使用 revise 而非 hot_fix。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n正文。"
        runner.writer.revise.return_value = "# 第一章\n\n修订后的正文。"

        runner.critic = MagicMock(spec=Critic)
        # 不合格但没有锚定问题
        bad_eval = make_evaluation(75)
        bad_eval.anchored_issues = []  # has_anchored_issues 是计算属性，置空列表即可
        runner.critic.evaluate.side_effect = [bad_eval, make_evaluation(85)]

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter("ch_001", "四人相遇")

        # hot_fix 不应被调用
        runner.writer.hot_fix.assert_not_called()
        # revise 应被调用
        runner.writer.revise.assert_called_once()


# ── 章节调度工具函数测试 ──


class TestSchedulingHelpers:
    """章节调度工具函数测试。"""

    def test_proposal_sort_key_finds_index(self) -> None:
        """测试 _proposal_sort_key 找到目标章节索引。"""
        from opennovel.core.auto_runner import _proposal_sort_key
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        chapters = [("ch_001", "第一章"), ("ch_002", "第二章"), ("ch_003", "第三章")]
        proposal = SchedulingProposal(
            action=SchedulingAction.SKIP,
            target_chapter_id="ch_002",
            rationale="测试",
        )
        assert _proposal_sort_key(proposal, chapters) == 1

    def test_proposal_sort_key_not_found(self) -> None:
        """测试 _proposal_sort_key 找不到时返回 -1。"""
        from opennovel.core.auto_runner import _proposal_sort_key
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        chapters = [("ch_001", "第一章")]
        proposal = SchedulingProposal(
            action=SchedulingAction.SKIP,
            target_chapter_id="ch_999",
            rationale="测试",
        )
        assert _proposal_sort_key(proposal, chapters) == -1

    def test_proposal_affects_future_true(self) -> None:
        """测试 _proposal_affects_future 判断未来的章节返回 True。"""
        from opennovel.core.auto_runner import _proposal_affects_future
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        chapters = [("ch_001", "1"), ("ch_002", "2"), ("ch_003", "3")]
        proposal = SchedulingProposal(
            action=SchedulingAction.SKIP,
            target_chapter_id="ch_002",
            rationale="测试",
        )
        # ch_002 在索引 1，当前已完成到索引 0 → 属于未来章节
        assert _proposal_affects_future(proposal, chapters, 0) is True

    def test_proposal_affects_future_false(self) -> None:
        """测试 _proposal_affects_future 判断已完成的章节返回 False。"""
        from opennovel.core.auto_runner import _proposal_affects_future
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        chapters = [("ch_001", "1"), ("ch_002", "2"), ("ch_003", "3")]
        proposal = SchedulingProposal(
            action=SchedulingAction.SKIP,
            target_chapter_id="ch_001",
            rationale="测试",
        )
        # ch_001 在索引 0，当前已完成到索引 0 → 不属于未来章节
        assert _proposal_affects_future(proposal, chapters, 0) is False

    def test_generate_new_chapter_id(self) -> None:
        """测试 _generate_new_chapter_id 生成不重复的 ID。"""
        from opennovel.core.auto_runner import _generate_new_chapter_id

        assert _generate_new_chapter_id({"ch_001", "ch_003"}) == "ch_004"
        assert _generate_new_chapter_id(set()) == "ch_001"
        assert _generate_new_chapter_id({"ch_001", "ch_002", "ch_010"}) == "ch_011"


# ── 章节调度集成测试 ──


class TestSchedulingProposals:
    """AutoRunner 章节调度集成测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_apply_skip_proposal(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 _apply_skip_proposal 移除目标章节。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chapters = [("ch_001", "1"), ("ch_002", "2"), ("ch_003", "3")]

        proposal = SchedulingProposal(
            action=SchedulingAction.SKIP,
            target_chapter_id="ch_002",
            rationale="测试跳过",
        )
        result = runner._apply_skip_proposal(chapters, proposal)

        assert len(result) == 2
        assert result[0][0] == "ch_001"
        assert result[1][0] == "ch_003"

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_apply_skip_proposal_not_found(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 _apply_skip_proposal 找不到目标时不改动。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chapters = [("ch_001", "1"), ("ch_002", "2")]

        proposal = SchedulingProposal(
            action=SchedulingAction.SKIP,
            target_chapter_id="ch_999",
            rationale="不存在的章节",
        )
        result = runner._apply_skip_proposal(chapters, proposal)
        assert len(result) == 2
        assert result == chapters

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_apply_insert_proposal(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 _apply_insert_proposal 在目标前插入新章节。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chapters = [("ch_001", "1"), ("ch_002", "2"), ("ch_003", "3")]

        proposal = SchedulingProposal(
            action=SchedulingAction.INSERT,
            target_chapter_id="ch_002",
            rationale="需要补充过渡章节",
            new_chapter_hint="## 补充章节\n\n角色内心独白。",
        )
        result = runner._apply_insert_proposal(chapters, proposal, current_index=0)

        assert len(result) == 4
        assert result[0][0] == "ch_001"
        assert result[1][0] == "ch_004"  # 新生成的 ID
        assert "补充章节" in result[1][1]
        assert result[2][0] == "ch_002"
        assert result[3][0] == "ch_003"

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_apply_insert_proposal_empty_hint(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 _apply_insert_proposal 空 hint 时不插入。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chapters = [("ch_001", "1"), ("ch_002", "2")]

        proposal = SchedulingProposal(
            action=SchedulingAction.INSERT,
            target_chapter_id="ch_002",
            rationale="测试",
            new_chapter_hint="",
        )
        result = runner._apply_insert_proposal(chapters, proposal, current_index=0)
        assert len(result) == 2

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_apply_scheduling_proposals_skip_only_future(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 _apply_scheduling_proposals 只操作未来章节。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chapters = [("ch_001", "1"), ("ch_002", "2"), ("ch_003", "3")]

        proposals = [
            SchedulingProposal(
                action=SchedulingAction.SKIP,
                target_chapter_id="ch_001",  # 已完成的章节（索引 0）
                rationale="已完成",
            ),
            SchedulingProposal(
                action=SchedulingAction.SKIP,
                target_chapter_id="ch_003",  # 未来的章节（索引 2）
                rationale="未来跳过",
            ),
        ]
        result = runner._apply_scheduling_proposals(chapters, proposals, current_index=0)

        # ch_001 已完成不应被跳过，ch_003 应被跳过
        assert len(result) == 2
        assert result[0][0] == "ch_001"  # 保留
        assert result[1][0] == "ch_002"  # 保留

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_apply_scheduling_proposals_insert_and_skip(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 _apply_scheduling_proposals 同时处理插入和跳过。"""
        from opennovel.schemas.director import SchedulingAction, SchedulingProposal

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chapters = [("ch_001", "1"), ("ch_002", "2"), ("ch_003", "3")]

        proposals = [
            SchedulingProposal(
                action=SchedulingAction.INSERT,
                target_chapter_id="ch_003",
                rationale="插入过渡章",
                new_chapter_hint="## 过渡章\n\n缓冲章节。",
            ),
            SchedulingProposal(
                action=SchedulingAction.SKIP,
                target_chapter_id="ch_002",
                rationale="跳过无必要章节",
            ),
        ]
        result = runner._apply_scheduling_proposals(chapters, proposals, current_index=0)

        # ch_002 被跳过，ch_003 前插入新章 → 最终 3 章（ch_001, ch_004, ch_003）
        assert len(result) == 3
        assert result[0][0] == "ch_001"
        assert result[1][0] == "ch_004"  # 插入的补充章节
        assert result[2][0] == "ch_003"

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_apply_scheduling_proposals_empty(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试空提议列表不改变章节列表。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        chapters = [("ch_001", "1"), ("ch_002", "2")]
        result = runner._apply_scheduling_proposals(chapters, [], current_index=0)
        assert result == chapters
        assert len(result) == 2


# ── 知识缺口检测集成测试 ──


class TestAutoRunnerKnowledgeGaps:
    """AutoRunner 知识缺口检测集成测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_auto_runner_has_tool_registry(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 AutoRunner 初始化时创建 ToolRegistry。"""
        from opennovel.core.tool_registry import ToolRegistry

        runner = AutoRunner(project_root=empty_project_root, config=default_config)
        assert hasattr(runner, "tool_registry")
        assert isinstance(runner.tool_registry, ToolRegistry)
        # 默认可用数据源
        sources = runner.tool_registry.get_available_sources()
        assert "canon" in sources

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_knowledge_gaps_detected_and_injected(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试 run_chapter 中知识缺口被检测并注入 write 调用。"""
        from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeSource

        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        # Mock Writer
        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline("ch_001")
        runner.writer.write.return_value = "# 第一章\n\n正文。"
        runner.writer.detect_knowledge_gaps.return_value = [
            KnowledgeNeed(
                concept="char_001", source=KnowledgeSource.CHARACTER, character_id="char_001"
            ),
        ]
        runner.writer.format_knowledge_results.return_value = "【补充】角色状态信息。"

        # Mock Critic
        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(85)

        # Mock Manager
        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        # 创建角色文件供 ToolRegistry 查询
        (runner.project_root / "characters" / "char_001.md").write_text(
            "---\nid: char_001\nname: 测试角色\n---\n正文", encoding="utf-8"
        )

        runner.run_chapter("ch_001", "四人在加油站相遇")

        # 验证 detect_knowledge_gaps 被调用
        runner.writer.detect_knowledge_gaps.assert_called_once()
        # 验证 format_knowledge_results 被调用
        runner.writer.format_knowledge_results.assert_called_once()
        # 验证 write 收到 additional_knowledge
        write_call = runner.writer.write.call_args
        assert "additional_knowledge" in write_call[1] or len(write_call[0]) >= 4

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_no_gaps_no_additional_knowledge(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        empty_project_root: Path,
        default_config: LoomConfig,
    ) -> None:
        """测试无知识缺口时不注入额外信息。"""
        runner = AutoRunner(project_root=empty_project_root, config=default_config)

        runner.writer = MagicMock(spec=Writer)
        runner.writer.think.return_value = make_outline()
        runner.writer.write.return_value = "# 第一章\n\n正文。"
        runner.writer.detect_knowledge_gaps.return_value = []  # 无缺口
        runner.writer.format_knowledge_results.return_value = ""

        runner.critic = MagicMock(spec=Critic)
        runner.critic.evaluate.return_value = make_evaluation(85)

        runner.manager = MagicMock(spec=Manager)
        runner.manager.update.return_value = make_manager_result()

        runner.run_chapter("ch_001", "四人在加油站相遇")

        # detect_knowledge_gaps 被调用但返回空
        runner.writer.detect_knowledge_gaps.assert_called_once()
        # format_knowledge_results 不应被调用（无结果）
        runner.writer.format_knowledge_results.assert_not_called()
