"""故障分析器 — 自动诊断生成失败原因并推荐恢复操作。

分析错误上下文、追溯因果链、排名可能原因、推荐恢复策略。

详见 docs/adr/0012-real-time-self-healing-system.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class FaultType(str, Enum):
    """故障类型枚举。"""

    LLM_TIMEOUT = "llm_timeout"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    CANON_CONFLICT = "canon_conflict"
    AGENT_ERROR = "agent_error"
    SNAPSHOT_ERROR = "snapshot_error"
    UNKNOWN = "unknown"


class RecoveryActionType(str, Enum):
    """恢复操作类型。"""

    RETRY = "retry"
    SWITCH_MODEL = "switch_model"
    ROLLBACK = "rollback"
    REDUCE_BUDGET = "reduce_budget"
    SKIP_CHAPTER = "skip_chapter"
    MANUAL_INTERVENTION = "manual_intervention"


@dataclass
class FaultCause:
    """故障的可能原因。"""

    fault_type: FaultType
    probability: float  # 0.0~1.0
    description: str
    evidence: str = ""


@dataclass
class RecoveryAction:
    """推荐的恢复操作。"""

    action_type: RecoveryActionType
    description: str
    risk: str = "low"  # low / medium / high


@dataclass
class FaultReport:
    """故障分析报告。"""

    chapter_id: str = ""
    failed_phase: str = ""  # think / write / evaluate / update / analyze
    error_message: str = ""
    causes: list[FaultCause] = field(default_factory=list)
    recovery_actions: list[RecoveryAction] = field(default_factory=list)
    related_logs: list[str] = field(default_factory=list)


class FaultAnalyzer:
    """故障分析器。

    基于错误上下文自动诊断失败原因，输出可能原因排名和推荐恢复操作。

    使用方式:
        analyzer = FaultAnalyzer(project_root)
        report = analyzer.analyze("ch_003", "write", TimeoutError("..."))
        for action in report.recovery_actions:
            print(f"[{action.risk}] {action.description}")
    """

    def __init__(self, project_root: Path) -> None:
        """初始化故障分析器。

        Args:
            project_root: 项目根目录
        """
        self.project_root = project_root

    def analyze(
        self,
        chapter_id: str,
        failed_phase: str,
        error: Exception | str,
    ) -> FaultReport:
        """分析故障原因并生成恢复建议。

        Args:
            chapter_id: 失败所在章节 ID
            failed_phase: 失败的流水线阶段
            error: 异常对象或错误消息字符串

        Returns:
            FaultReport 分析报告
        """
        error_msg = str(error) if isinstance(error, Exception) else error
        error_type = type(error).__name__ if isinstance(error, Exception) else "str"

        report = FaultReport(
            chapter_id=chapter_id,
            failed_phase=failed_phase,
            error_message=error_msg,
        )

        # ── 1. 故障分类 ──
        causes = self._classify_fault(error_msg, error_type, failed_phase)
        report.causes = sorted(causes, key=lambda c: c.probability, reverse=True)

        # ── 2. 推荐恢复操作 ──
        report.recovery_actions = self._recommend_recovery(causes, failed_phase)

        # ── 3. 收集相关日志 ──
        report.related_logs = self._collect_related_logs(chapter_id, failed_phase)

        return report

    def _classify_fault(
        self,
        error_msg: str,
        error_type: str,
        failed_phase: str,
    ) -> list[FaultCause]:
        """根据错误特征分类故障类型。

        Args:
            error_msg: 错误消息
            error_type: 错误类型名
            failed_phase: 失败阶段

        Returns:
            可能原因列表（按概率降序）
        """
        causes: list[FaultCause] = []
        msg_lower = error_msg.lower()

        # LLM 超时
        if any(kw in msg_lower for kw in ["timeout", "timed out", "timed_out"]):
            causes.append(
                FaultCause(
                    fault_type=FaultType.LLM_TIMEOUT,
                    probability=0.9,
                    description="LLM API 调用超时",
                    evidence=f"错误类型: {error_type}, 阶段: {failed_phase}",
                )
            )

        # Token 预算超限
        if any(kw in msg_lower for kw in ["token", "budget", "context length", "max_tokens"]):
            causes.append(
                FaultCause(
                    fault_type=FaultType.TOKEN_BUDGET_EXCEEDED,
                    probability=0.85,
                    description="Token 预算超限或上下文过长",
                    evidence=f"错误: {error_msg[:200]}",
                )
            )

        # CANON 冲突
        if any(kw in msg_lower for kw in ["canon", "violation", "rule"]):
            causes.append(
                FaultCause(
                    fault_type=FaultType.CANON_CONFLICT,
                    probability=0.8,
                    description="内容违反 CANON 世界观规则",
                    evidence=f"阶段: {failed_phase}",
                )
            )

        # Agent 内部错误
        if failed_phase in ("think", "write", "evaluate", "update", "analyze"):
            causes.append(
                FaultCause(
                    fault_type=FaultType.AGENT_ERROR,
                    probability=0.6,
                    description=f"{failed_phase} 阶段执行异常",
                    evidence=f"错误类型: {error_type}",
                )
            )

        # 兜底：未知故障
        if not causes:
            causes.append(
                FaultCause(
                    fault_type=FaultType.UNKNOWN,
                    probability=0.5,
                    description="未识别的故障类型",
                    evidence=error_msg[:200],
                )
            )

        return causes

    def _recommend_recovery(
        self,
        causes: list[FaultCause],
        failed_phase: str,
    ) -> list[RecoveryAction]:
        """根据故障原因推荐恢复操作。

        Args:
            causes: 排序后的故障原因列表
            failed_phase: 失败阶段

        Returns:
            推荐操作列表
        """
        actions: list[RecoveryAction] = []

        for cause in causes[:3]:  # 只看 Top 3 原因
            if cause.fault_type == FaultType.LLM_TIMEOUT:
                actions.append(
                    RecoveryAction(
                        action_type=RecoveryActionType.RETRY,
                        description="重试当前操作（最多 3 次）",
                        risk="low",
                    )
                )
                actions.append(
                    RecoveryAction(
                        action_type=RecoveryActionType.SWITCH_MODEL,
                        description="切换到备选模型重试（备选模型通常更稳定）",
                        risk="medium",
                    )
                )

            elif cause.fault_type == FaultType.TOKEN_BUDGET_EXCEEDED:
                actions.append(
                    RecoveryAction(
                        action_type=RecoveryActionType.REDUCE_BUDGET,
                        description="缩小上下文窗口后重试（减少注入知识量）",
                        risk="low",
                    )
                )

            elif cause.fault_type == FaultType.CANON_CONFLICT:
                actions.append(
                    RecoveryAction(
                        action_type=RecoveryActionType.MANUAL_INTERVENTION,
                        description="人工检查 CANON 冲突并决定是否豁免或修改",
                        risk="medium",
                    )
                )

            elif cause.fault_type == FaultType.AGENT_ERROR:
                actions.append(
                    RecoveryAction(
                        action_type=RecoveryActionType.RETRY,
                        description=f"重试 {failed_phase} 阶段",
                        risk="low",
                    )
                )

        # 兜底
        if not actions:
            actions.append(
                RecoveryAction(
                    action_type=RecoveryActionType.ROLLBACK,
                    description="回滚到上一安全快照后重新开始本章",
                    risk="high",
                )
            )

        return actions

    def _collect_related_logs(
        self,
        chapter_id: str,
        failed_phase: str,
        max_lines: int = 20,
    ) -> list[str]:
        """收集与故障相关的日志条目。

        Args:
            chapter_id: 章节 ID
            failed_phase: 失败阶段
            max_lines: 最大收集行数

        Returns:
            日志行列表
        """
        logs: list[str] = []
        log_dir = self.project_root / "logs"

        if not log_dir.exists():
            return logs

        # 尝试从 run_log.md 获取最近信息
        run_log = self.project_root / "run_log.md"
        if run_log.exists():
            try:
                lines = run_log.read_text(encoding="utf-8").split("\n")
                # 获取最后 N 行
                logs = [f"run_log: {l}" for l in lines[-max_lines:]]
            except Exception:
                pass

        # 尝试从 reasoning 日志获取
        reasoning_dir = log_dir / "reasoning"
        if reasoning_dir.exists():
            for trace_file in sorted(reasoning_dir.glob("*.json"), reverse=True):
                try:
                    logs.append(f"reasoning: {trace_file.name}")
                    if len(logs) >= max_lines:
                        break
                except Exception:
                    continue

        return logs
