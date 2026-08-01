"""安全围栏 — Agent 自治的约束边界。

基于 ADR 0006 安全围栏设计：
- 递归深度防护：防止 Agent 无限嵌套调用
- Token 预算追踪：限制单次自治调用的 Token 消耗
- 超时熔断：Agent 自治操作超过时限自动终止
- Canon 不可违背：校验 Agent 生成文本是否违反核心世界观规则

使用方式:
    fence = SafetyFence()
    with fence.autonomous_call("writer", max_tokens=4000):
        result = writer.hot_fix(...)
    if fence.is_violated():
        logger.warning(fence.violations)

    # Canon 校验
    fence.check_canon_integrity("writer", agent_text, canon_dir)
"""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

# 默认安全围栏参数
DEFAULT_MAX_RECURSION_DEPTH = 3
DEFAULT_MAX_TOKENS_PER_CALL = 4000
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_FORBIDDEN_MODIFICATIONS: list[str] = field(default_factory=lambda: [])


@dataclass
class SafetyFenceConfig:
    """安全围栏配置参数。

    Attributes:
        max_recursion_depth: 最大递归深度，防止 Agent 无限嵌套调用
        max_tokens_per_call: 单次自治调用的 Token 上限
        timeout_seconds: 单次自治调用的超时时间（秒）
        forbidden_modifications: 禁止修改的 CANON 元素列表
        canon_dir: Canon 设定目录（启用世界观规则校验时需要）
        enabled: 是否启用安全围栏
    """

    max_recursion_depth: int = DEFAULT_MAX_RECURSION_DEPTH
    max_tokens_per_call: int = DEFAULT_MAX_TOKENS_PER_CALL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    forbidden_modifications: list[str] = field(default_factory=list)
    canon_dir: str | None = None
    enabled: bool = True


@dataclass
class SafetyViolation:
    """安全围栏违规记录。"""

    rule: str
    """违反的规则名称：recursion_depth / token_budget / timeout / canon_violation"""

    agent: str
    """违规的 Agent 名称"""

    detail: str
    """违规详情"""

    timestamp: float = field(default_factory=time.time)
    """违规时间戳"""


class SafetyFence:
    """安全围栏 — 约束 Agent 自治行为的边界。

    线程安全（单线程场景），维护递归深度栈和 Token 消耗计数器。
    支持世界观规则校验（Canon Integrity Check）。
    """

    def __init__(self, config: SafetyFenceConfig | None = None) -> None:
        self.config = config or SafetyFenceConfig()
        self._recursion_depth: int = 0
        self._token_used: int = 0
        self._call_start_time: float = 0.0
        self.violations: list[SafetyViolation] = []
        self._canon_checker: Any = None  # 延迟加载 CanonChecker

    # ── 属性 ─────────────────────────────────────────────────────────────

    @property
    def recursion_depth(self) -> int:
        """当前递归深度。"""
        return self._recursion_depth

    @property
    def token_used(self) -> int:
        """当前自治调用中已消耗的 Token 数。"""
        return self._token_used

    @property
    def is_within_budget(self) -> bool:
        """当前 Token 消耗是否在预算内。"""
        return self._token_used <= self.config.max_tokens_per_call

    @property
    def is_within_depth(self) -> bool:
        """当前递归深度是否在限制内。"""
        return self._recursion_depth <= self.config.max_recursion_depth

    @property
    def is_within_timeout(self) -> bool:
        """当前调用是否未超时。"""
        if self._call_start_time == 0:
            return True
        return (time.time() - self._call_start_time) <= self.config.timeout_seconds

    # ── 检查方法 ─────────────────────────────────────────────────────────

    def check_recursion_depth(self, agent: str) -> bool:
        """检查递归深度是否超限。

        Args:
            agent: Agent 名称

        Returns:
            True 表示在限制内
        """
        if not self.config.enabled:
            return True
        if self._recursion_depth > self.config.max_recursion_depth:
            self.violations.append(
                SafetyViolation(
                    rule="recursion_depth",
                    agent=agent,
                    detail=(
                        f"递归深度 {self._recursion_depth} "
                        f"超过上限 {self.config.max_recursion_depth}"
                    ),
                )
            )
            logger.warning("安全围栏: Agent %s 递归深度 %d 超限", agent, self._recursion_depth)
            return False
        return True

    def check_token_budget(self, agent: str, additional_tokens: int = 0) -> bool:
        """检查 Token 预算是否超限。

        Args:
            agent: Agent 名称
            additional_tokens: 即将消耗的额外 Token 数

        Returns:
            True 表示在预算内
        """
        if not self.config.enabled:
            return True
        projected = self._token_used + additional_tokens
        if projected > self.config.max_tokens_per_call:
            self.violations.append(
                SafetyViolation(
                    rule="token_budget",
                    agent=agent,
                    detail=(f"预计 Token {projected} 超过上限 {self.config.max_tokens_per_call}"),
                )
            )
            logger.warning(
                "安全围栏: Agent %s Token 预算超限 (%d > %d)",
                agent,
                projected,
                self.config.max_tokens_per_call,
            )
            return False
        return True

    def check_timeout(self, agent: str) -> bool:
        """检查是否超时。

        Args:
            agent: Agent 名称

        Returns:
            True 表示未超时
        """
        if not self.config.enabled or self._call_start_time == 0:
            return True
        elapsed = time.time() - self._call_start_time
        if elapsed > self.config.timeout_seconds:
            self.violations.append(
                SafetyViolation(
                    rule="timeout",
                    agent=agent,
                    detail=f"执行时间 {elapsed:.1f}s 超过上限 {self.config.timeout_seconds}s",
                )
            )
            logger.warning(
                "安全围栏: Agent %s 超时 (%.1fs > %ds)",
                agent,
                elapsed,
                self.config.timeout_seconds,
            )
            return False
        return True

    def check_all(self, agent: str, additional_tokens: int = 0) -> bool:
        """执行全部安全检查。

        Args:
            agent: Agent 名称
            additional_tokens: 即将消耗的额外 Token 数

        Returns:
            True 表示全部检查通过
        """
        checks = [
            self.check_recursion_depth(agent),
            self.check_token_budget(agent, additional_tokens),
            self.check_timeout(agent),
        ]
        return all(checks)

    def check_canon_integrity(
        self,
        agent: str,
        text: str,
        canon_dir: Path | None = None,
        strict: bool = False,
    ) -> bool:
        """检查文本是否违反世界观规则（Canon Integrity Check）。

        从 canon 目录加载世界观规则，对 Agent 生成的文本进行校验。
        violation 级别以上视为违反，suggestion 级别仅提醒不阻断。

        Args:
            agent: Agent 名称
            text: 待校验的 Agent 生成文本
            canon_dir: Canon 设定目录路径。为 None 时尝试从 config 获取。
            strict: 严格模式。True 时 suggestion 级别也视为违反。

        Returns:
            True 表示未违反规则（或无法执行校验）
        """
        if not self.config.enabled:
            return True

        # 确定 canon 目录
        resolved_dir = self._resolve_canon_dir(canon_dir)
        if resolved_dir is None:
            logger.info("安全围栏: 无 canon 目录，跳过世界观规则校验")
            return True

        # 延迟加载 CanonChecker
        if self._canon_checker is None:
            try:
                from opennovel.core.canon_checker import CanonChecker

                self._canon_checker = CanonChecker()
            except ImportError:
                logger.warning("安全围栏: CanonChecker 不可用，跳过世界观规则校验")
                return True

        rules = self._canon_checker.load_rules(resolved_dir)
        if not rules:
            return True

        violations = self._canon_checker.check_text(text, rules)
        if not violations:
            return True

        blocking_violations = 0
        for v in violations:
            if v.severity == "violation":
                blocking_violations += 1
            elif v.severity == "warning":
                blocking_violations += 1
            elif v.severity == "suggestion" and strict:
                blocking_violations += 1

            self.violations.append(
                SafetyViolation(
                    rule="canon_violation",
                    agent=agent,
                    detail=f"[{v.severity}] 规则「{v.rule}」: {v.detail} | 片段: {v.snippet[:60]}",
                )
            )

        if blocking_violations > 0:
            logger.warning(
                "安全围栏: Agent %s 违反 %d 条世界观规则",
                agent,
                blocking_violations,
            )
            return False

        # 仅有 suggestion 级别提醒，不阻断
        return True

    def _resolve_canon_dir(self, canon_dir: Path | None) -> Path | None:
        """解析 canon 目录路径。

        Args:
            canon_dir: 传入的目录路径

        Returns:
            解析后的目录路径，无法解析则返回 None
        """
        if canon_dir is not None:
            return canon_dir if canon_dir.exists() else None
        if self.config.canon_dir:
            p = Path(self.config.canon_dir)
            return p if p.exists() else None
        return None

    def is_violated(self) -> bool:
        """是否有任何违规记录。"""
        return len(self.violations) > 0

    def clear_violations(self) -> None:
        """清除所有违规记录。"""
        self.violations.clear()

    # ── 状态管理 ─────────────────────────────────────────────────────────

    def record_tokens(self, tokens: int) -> None:
        """记录消耗的 Token 数。

        Args:
            tokens: 消耗的 Token 数
        """
        self._token_used += tokens
        logger.debug("安全围栏: 记录 Token +%d (总计 %d)", tokens, self._token_used)

    def reset_call(self) -> None:
        """重置单次调用的计数（递归深度和 Token）。"""
        self._recursion_depth = 0
        self._token_used = 0
        self._call_start_time = 0.0

    # ── 上下文管理器 ─────────────────────────────────────────────────────

    @contextmanager
    def autonomous_call(
        self,
        agent: str,
        max_tokens: int | None = None,
    ) -> Generator[Any, Any, Any]:
        """Agent 自治调用的上下文管理器。

        自动管理递归深度、Token 预算和超时。

        Args:
            agent: Agent 名称
            max_tokens: 本次调用的 Token 上限（覆盖配置）

        Yields:
            None

        Raises:
            RuntimeError: 当安全检查失败时
        """
        if not self.config.enabled:
            yield
            return

        # 递归深度递增
        self._recursion_depth += 1

        # 首次调用时记录开始时间
        if self._call_start_time == 0:
            self._call_start_time = time.time()

        # 预算覆盖
        original_max_tokens = self.config.max_tokens_per_call
        if max_tokens is not None:
            self.config.max_tokens_per_call = max_tokens

        try:
            if not self.check_recursion_depth(agent):
                raise RuntimeError(
                    f"安全围栏: {agent} 递归深度超限 "
                    f"({self._recursion_depth}/{self.config.max_recursion_depth})"
                )
            if not self.check_timeout(agent):
                raise RuntimeError(f"安全围栏: {agent} 超时 ({self.config.timeout_seconds}s)")
            yield
        finally:
            self._recursion_depth -= 1
            if max_tokens is not None:
                self.config.max_tokens_per_call = original_max_tokens

    def __repr__(self) -> str:
        return (
            f"SafetyFence(depth={self._recursion_depth}/"
            f"{self.config.max_recursion_depth}, "
            f"tokens={self._token_used}/"
            f"{self.config.max_tokens_per_call}, "
            f"violations={len(self.violations)})"
        )
