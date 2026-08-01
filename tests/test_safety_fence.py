"""安全围栏模块测试 - SafetyFence 约束系统。"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opennovel.core.safety_fence import SafetyFence, SafetyFenceConfig, SafetyViolation


class TestSafetyFenceConfig:
    """SafetyFenceConfig 配置测试。"""

    def test_default_values(self) -> None:
        """测试默认值。"""
        config = SafetyFenceConfig()
        assert config.max_recursion_depth == 3
        assert config.max_tokens_per_call == 4000
        assert config.timeout_seconds == 120
        assert config.forbidden_modifications == []
        assert config.enabled is True

    def test_custom_values(self) -> None:
        """测试自定义值。"""
        config = SafetyFenceConfig(
            max_recursion_depth=1,
            max_tokens_per_call=2000,
            timeout_seconds=30,
            forbidden_modifications=["canon/world_rules.md"],
            enabled=False,
        )
        assert config.max_recursion_depth == 1
        assert config.max_tokens_per_call == 2000
        assert config.timeout_seconds == 30
        assert config.forbidden_modifications == ["canon/world_rules.md"]
        assert config.enabled is False


class TestSafetyFenceInit:
    """SafetyFence 初始化测试。"""

    def test_default_init(self) -> None:
        """测试默认初始化。"""
        fence = SafetyFence()
        assert fence.recursion_depth == 0
        assert fence.token_used == 0
        assert fence.violations == []

    def test_custom_config(self) -> None:
        """测试自定义配置。"""
        config = SafetyFenceConfig(max_recursion_depth=5)
        fence = SafetyFence(config)
        assert fence.config.max_recursion_depth == 5

    def test_disabled_no_violations(self) -> None:
        """测试禁用时不产生违规记录。"""
        config = SafetyFenceConfig(enabled=False)
        fence = SafetyFence(config)
        assert fence.check_recursion_depth("writer") is True
        assert fence.check_token_budget("writer", 99999) is True
        assert fence.check_timeout("writer") is True
        assert fence.violations == []


class TestRecursionDepth:
    """递归深度检查测试。"""

    def test_within_limit(self) -> None:
        """测试深度在限制内。"""
        fence = SafetyFence()
        fence._recursion_depth = 1
        assert fence.check_recursion_depth("writer") is True
        assert len(fence.violations) == 0

    def test_at_limit(self) -> None:
        """测试深度恰好等于上限时允许（depth == max → OK）。"""
        fence = SafetyFence()
        fence._recursion_depth = 3  # 等于默认上限 3
        # 当前深度 3 = 上限 3，尚未超过，应通过
        assert fence.check_recursion_depth("writer") is True
        assert len(fence.violations) == 0

    def test_exceeded_limit(self) -> None:
        """测试深度超过上限。"""
        config = SafetyFenceConfig(max_recursion_depth=2)
        fence = SafetyFence(config)
        fence._recursion_depth = 3  # 3 > 2 → 超限
        assert fence.check_recursion_depth("manager") is False
        assert len(fence.violations) == 1
        assert fence.violations[0].rule == "recursion_depth"
        assert fence.violations[0].agent == "manager"


class TestTokenBudget:
    """Token 预算检查测试。"""

    def test_within_budget(self) -> None:
        """测试 Token 在预算内。"""
        fence = SafetyFence()
        fence.record_tokens(2000)
        assert fence.check_token_budget("writer", 1000) is True  # 2000+1000=3000 <= 4000
        assert len(fence.violations) == 0

    def test_exceeded_budget(self) -> None:
        """测试 Token 超过预算。"""
        fence = SafetyFence()
        fence.record_tokens(3500)
        assert fence.check_token_budget("writer", 1000) is False  # 3500+1000=4500 > 4000
        assert len(fence.violations) == 1
        assert fence.violations[0].rule == "token_budget"

    def test_exact_budget(self) -> None:
        """测试恰好用尽预算。"""
        fence = SafetyFence()
        fence.record_tokens(4000)
        assert fence.check_token_budget("writer", 0) is True  # 4000+0=4000
        assert len(fence.violations) == 0

    def test_custom_budget(self) -> None:
        """测试自定义预算。"""
        config = SafetyFenceConfig(max_tokens_per_call=1000)
        fence = SafetyFence(config)
        fence.record_tokens(800)
        assert fence.check_token_budget("writer", 200) is True
        assert fence.check_token_budget("writer", 300) is False


class TestTimeout:
    """超时检查测试。"""

    def test_within_timeout(self) -> None:
        """测试未超时。"""
        fence = SafetyFence()
        fence._call_start_time = time.time()
        assert fence.check_timeout("writer") is True

    def test_exceeded_timeout(self) -> None:
        """测试超时。"""
        fence = SafetyFence()
        fence._call_start_time = time.time() - 200  # 200 秒前 > 120 秒上限
        assert fence.check_timeout("writer") is False
        assert len(fence.violations) == 1
        assert fence.violations[0].rule == "timeout"

    def test_no_start_time(self) -> None:
        """测试未设置开始时间时不超时。"""
        fence = SafetyFence()
        assert fence.check_timeout("writer") is True


class TestCheckAll:
    """组合检查测试。"""

    def test_all_pass(self) -> None:
        """测试全部检查通过。"""
        fence = SafetyFence()
        assert fence.check_all("writer") is True

    def test_one_fails(self) -> None:
        """测试单项失败。"""
        fence = SafetyFence()
        fence.record_tokens(99999)
        assert fence.check_all("writer") is False

    def test_records_violation(self) -> None:
        """测试失败时记录违规。"""
        config = SafetyFenceConfig(max_recursion_depth=1)
        fence = SafetyFence(config)
        fence._recursion_depth = 2  # 2 > 1 → 超限
        assert fence.check_all("critic") is False
        assert len(fence.violations) == 1


class TestAutonomousCall:
    """autonomous_call 上下文管理器测试。"""

    def test_success(self) -> None:
        """测试上下文管理器正常执行。"""
        fence = SafetyFence()
        with fence.autonomous_call("writer"):
            assert fence.recursion_depth == 1
            fence.record_tokens(1000)
        assert fence.recursion_depth == 0
        assert fence.token_used == 1000

    def test_nested_depth_management(self) -> None:
        """测试嵌套调用的深度管理。"""
        fence = SafetyFence()
        with fence.autonomous_call("writer"):
            assert fence.recursion_depth == 1
            with fence.autonomous_call("writer"):
                assert fence.recursion_depth == 2
            assert fence.recursion_depth == 1
        assert fence.recursion_depth == 0

    def test_exception_exit(self) -> None:
        """测试异常退出时深度恢复。"""
        fence = SafetyFence()
        try:
            with fence.autonomous_call("writer"):
                assert fence.recursion_depth == 1
                raise ValueError("测试异常")
        except ValueError:
            pass
        assert fence.recursion_depth == 0

    def test_max_depth_override(self) -> None:
        """测试上下文管理器的 max_tokens 覆盖配置。"""
        config = SafetyFenceConfig(max_tokens_per_call=9999)
        fence = SafetyFence(config)
        with fence.autonomous_call("writer", max_tokens=2000):
            assert fence.config.max_tokens_per_call == 2000
        assert fence.config.max_tokens_per_call == 9999

    def test_recursion_depth_violation(self) -> None:
        """测试递归深度超限抛出异常。

        max_recursion_depth=1 时，允许 1 层嵌套（depth 0→1），
        第二次嵌套（depth 1→2）超过上限 1 应触发违规。
        """
        config = SafetyFenceConfig(max_recursion_depth=1)
        fence = SafetyFence(config)
        with pytest.raises(RuntimeError, match="递归深度超限"):
            with fence.autonomous_call("writer"):
                # 第一层：depth=1，1 ≤ 1 → OK
                with fence.autonomous_call("writer"):
                    # 第二层：depth=2，2 > 1 → 违规
                    pass
        assert fence.recursion_depth == 0


class TestRecordTokens:
    """Token 记录功能测试。"""

    def test_record_tokens_starts_at_zero(self) -> None:
        """测试初始 Token 为 0。"""
        fence = SafetyFence()
        assert fence.token_used == 0

    def test_record_tokens_accumulates(self) -> None:
        """测试 Token 累加。"""
        fence = SafetyFence()
        fence.record_tokens(100)
        assert fence.token_used == 100
        fence.record_tokens(200)
        assert fence.token_used == 300

    def test_is_within_budget(self) -> None:
        """测试预算状态属性。"""
        config = SafetyFenceConfig(max_tokens_per_call=100)
        fence = SafetyFence(config)
        assert fence.is_within_budget is True
        fence.record_tokens(50)
        assert fence.is_within_budget is True
        fence.record_tokens(51)
        assert fence.is_within_budget is False


class TestViolations:
    """违规记录功能测试。"""

    def test_is_violated(self) -> None:
        """测试违规状态检测。"""
        fence = SafetyFence()
        assert fence.is_violated() is False
        fence.record_tokens(99999)
        fence.check_token_budget("test", 0)
        assert fence.is_violated() is True

    def test_clear_violations(self) -> None:
        """测试清除违规记录。"""
        fence = SafetyFence()
        fence.record_tokens(99999)
        fence.check_token_budget("test", 0)
        assert len(fence.violations) > 0
        fence.clear_violations()
        assert fence.violations == []
        assert fence.is_violated() is False

    def test_reset_call(self) -> None:
        """测试重置调用计数。"""
        fence = SafetyFence()
        fence._recursion_depth = 2
        fence._token_used = 3000
        fence._call_start_time = 100.0
        fence.reset_call()
        assert fence.recursion_depth == 0
        assert fence.token_used == 0
        assert fence._call_start_time == 0.0


class TestSafetyViolation:
    """SafetyViolation 数据模型测试。"""

    def test_create_violation(self) -> None:
        """测试创建违规记录。"""
        v = SafetyViolation(
            rule="recursion_depth",
            agent="writer",
            detail="深度超限",
        )
        assert v.rule == "recursion_depth"
        assert v.agent == "writer"
        assert v.detail == "深度超限"
        assert v.timestamp > 0


class TestLoomConfigIntegration:
    """LoomConfig 与 SafetyFenceConfig 集成测试。"""

    def test_config_has_safety_fence(self) -> None:
        """测试 LoomConfig 包含 safety_fence。"""
        from opennovel.core.config import LoomConfig

        config = LoomConfig()
        assert hasattr(config, "safety_fence")
        assert config.safety_fence.enabled is True
        assert config.safety_fence.max_recursion_depth == 3

    def test_safety_fence_created_from_config(self) -> None:
        """测试从 config 创建 SafetyFence。"""
        from opennovel.core.config import LoomConfig

        config = LoomConfig()
        fence = SafetyFence(config.safety_fence)
        assert fence.config.max_recursion_depth == config.safety_fence.max_recursion_depth


class TestAutoRunnerSafetyIntegration:
    """AutoRunner 与安全围栏集成测试。"""

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_auto_runner_has_safety_fence(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试 AutoRunner 初始化时创建 SafetyFence。"""
        from opennovel.core.auto_runner import AutoRunner
        from opennovel.core.config import LoomConfig

        root = tmp_path / "project"
        root.mkdir()
        (root / "draft").mkdir()
        (root / "characters").mkdir()
        (root / "novel.yaml").write_text("model: test\n", encoding="utf-8")

        runner = AutoRunner(project_root=root, config=LoomConfig())
        assert hasattr(runner, "safety_fence")
        assert isinstance(runner.safety_fence, SafetyFence)
        assert runner.safety_fence.config.enabled is True

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_check_safety_passes(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试 _check_safety 在正常状态下通过。"""
        from opennovel.core.auto_runner import AutoRunner
        from opennovel.core.config import LoomConfig

        root = tmp_path / "project"
        root.mkdir()
        (root / "draft").mkdir()
        (root / "characters").mkdir()
        (root / "novel.yaml").write_text("model: test\n", encoding="utf-8")

        runner = AutoRunner(project_root=root, config=LoomConfig())
        assert runner._check_safety("writer") is True
        assert runner._check_safety("writer", additional_tokens=100) is True

    @patch("opennovel.core.auto_runner.LLMBus")
    @patch("opennovel.core.auto_runner.Retriever")
    @patch("opennovel.core.auto_runner.StateManager")
    def test_check_safety_fails_on_budget(
        self,
        mock_sm_cls: MagicMock,
        mock_retriever_cls: MagicMock,
        mock_llm_bus_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试 _check_safety 在预算超限时失败。"""
        from opennovel.core.auto_runner import AutoRunner
        from opennovel.core.config import LoomConfig
        from opennovel.core.safety_fence import SafetyFenceConfig

        root = tmp_path / "project"
        root.mkdir()
        (root / "draft").mkdir()
        (root / "characters").mkdir()
        (root / "novel.yaml").write_text("model: test\n", encoding="utf-8")

        config = LoomConfig()
        config.safety_fence = SafetyFenceConfig(max_tokens_per_call=100)
        runner = AutoRunner(project_root=root, config=config)
        runner.safety_fence.record_tokens(100)
        assert runner._check_safety("writer", additional_tokens=1) is False
