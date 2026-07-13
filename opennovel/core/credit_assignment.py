"""信用分配器 — 基于历史运行的 Agent 表现分析与优化建议。

从 MetricsStore 读取历史评价数据，分析各 Agent/模型/策略的表现趋势，
通过因果图追溯失败链，输出优化建议。

详见 docs/adr/0010-multi-agent-architecture-enhancement.md。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AgentPerformance:
    """单个 Agent 的历史表现统计。"""

    agent: str
    total_calls: int = 0
    success_count: int = 0  # 评分 ≥ 80 且无 critical issue
    failure_count: int = 0  # 评分 < 60 或 3 次以上 retry
    avg_score: float = 0.0
    avg_duration_ms: float = 0.0
    avg_tokens: int = 0
    trend: str = "stable"  # improving / stable / declining


@dataclass
class OptimizationSuggestion:
    """单条优化建议。"""

    category: str
    """分类：model / strategy / budget / agent_config"""

    agent: str
    """涉及的 Agent"""

    suggestion: str
    """人类可读的建议文本"""

    evidence: str
    """支持此建议的证据（数据引用）"""

    confidence: float = 0.5
    """置信度 0.0~1.0"""


@dataclass
class CreditReport:
    """信用分析完整报告。"""

    agent_performances: list[AgentPerformance] = field(default_factory=list)
    suggestions: list[OptimizationSuggestion] = field(default_factory=list)
    overall_trend: str = "stable"


class CreditAssigner:
    """信用分配器。

    分析 MetricsStore 中的历史数据，识别表现变化趋势，
    提供模型选择、策略调整等优化建议。

    使用方式:
        assigner = CreditAssigner(metrics_store)
        report = assigner.analyze()
        for s in report.suggestions:
            print(f"[{s.category}] {s.suggestion}")
    """

    def __init__(
        self,
        metrics_store: MetricsStore | None = None,
        project_root: Path | None = None,
    ) -> None:
        """初始化信用分配器。

        Args:
            metrics_store: MetricsStore 实例
            project_root: 项目根目录（自动加载 MetricsStore）
        """
        self._metrics = metrics_store
        self.project_root = project_root

    def _ensure_metrics(self) -> bool:
        """确保 MetricsStore 可用。"""
        if self._metrics is not None:
            return True
        if self.project_root is None:
            return False
        try:
            from opennovel.storage.metrics import MetricsStore
            metrics_path = self.project_root / ".novel.metrics.db"
            if not metrics_path.exists():
                return False
            self._metrics = MetricsStore(metrics_path)
            return True
        except Exception as e:
            logger.debug("MetricsStore 加载失败: %s", e)
            return False

    def analyze(self) -> CreditReport:
        """执行完整的信用分析。

        Returns:
            CreditReport 分析报告
        """
        report = CreditReport()

        if not self._ensure_metrics():
            report.suggestions.append(
                OptimizationSuggestion(
                    category="system",
                    agent="all",
                    suggestion="暂无足够历史数据进行分析。运行 novel auto 生成数据后重试。",
                    evidence="MetricsStore 不可用或无数据",
                    confidence=1.0,
                )
            )
            return report

        # 分析各 Agent 表现
        report.agent_performances = self._analyze_agents()

        # 生成优化建议
        report.suggestions = self._generate_suggestions(report.agent_performances)

        # 总体趋势
        report.overall_trend = self._detect_overall_trend(report.agent_performances)

        return report

    def _analyze_agents(self) -> list[AgentPerformance]:
        """从 MetricsStore 分析各 Agent 的历史表现。"""
        agents = ["writer", "critic", "manager", "director"]
        performances: list[AgentPerformance] = []

        for agent in agents:
            perf = AgentPerformance(agent=agent)

            try:
                # 从 agent_trace 表获取调用次数和耗时
                traces = self._get_agent_traces(agent)
                if not traces:
                    continue

                perf.total_calls = len(traces)
                durations = [t.get("duration_ms", 0) for t in traces if t.get("duration_ms")]
                if durations:
                    perf.avg_duration_ms = sum(durations) / len(durations)

                # 从 evaluation_history 获取全局评分数据（EvaluationHistory 不区分 Agent）
                scores = self._get_evaluation_scores()
                if scores:
                    perf.avg_score = sum(scores) / len(scores)
                    perf.success_count = sum(1 for s in scores if s >= 80)
                    perf.failure_count = sum(1 for s in scores if s < 60)

                    # 趋势检测：比较近 5 次和更早 5 次
                    if len(scores) >= 10:
                        recent = scores[-5:]
                        earlier = scores[-10:-5]
                        recent_avg = sum(recent) / len(recent)
                        earlier_avg = sum(earlier) / len(earlier)
                        diff = recent_avg - earlier_avg
                        if diff > 5:
                            perf.trend = "improving"
                        elif diff < -5:
                            perf.trend = "declining"
                        else:
                            perf.trend = "stable"

            except Exception as e:
                logger.debug("Agent %s 分析失败: %s", agent, e)

            performances.append(perf)

        return performances

    def _generate_suggestions(
        self,
        performances: list[AgentPerformance],
    ) -> list[OptimizationSuggestion]:
        """基于表现数据生成优化建议。"""
        suggestions: list[OptimizationSuggestion] = []

        for perf in performances:
            # 建议 1：表现持续下降的 Agent
            if perf.trend == "declining" and perf.total_calls >= 10:
                # 收集全局评分用于计算实际下降幅度
                scores = self._get_evaluation_scores()
                if len(scores) >= 10:
                    recent_avg = sum(scores[-5:]) / 5
                    earlier_avg = sum(scores[-10:-5]) / 5
                    score_diff = abs(recent_avg - earlier_avg)
                else:
                    score_diff = 0.0
                suggestions.append(
                    OptimizationSuggestion(
                        category="model",
                        agent=perf.agent,
                        suggestion=(
                            f"{perf.agent} Agent 近期评分呈下降趋势 "
                            f"（降幅 {score_diff:.1f} 分），"
                            f"建议尝试切换备选模型"
                        ),
                        evidence=f"基于 {perf.total_calls} 次调用的评分历史",
                        confidence=0.7,
                    )
                )

            # 建议 2：高失败率
            if perf.total_calls >= 5:
                failure_rate = perf.failure_count / max(perf.total_calls, 1)
                if failure_rate > 0.3:
                    suggestions.append(
                        OptimizationSuggestion(
                            category="agent_config",
                            agent=perf.agent,
                            suggestion=(
                                f"{perf.agent} Agent 失败率 {failure_rate:.0%}，"
                                f"建议检查 Prompt 配置或增加重试次数"
                            ),
                            evidence=f"{perf.failure_count}/{perf.total_calls} 次调用失败",
                            confidence=0.8,
                        )
                    )

        return suggestions

    def _detect_overall_trend(self, performances: list[AgentPerformance]) -> str:
        """检测整体趋势。"""
        trends = [p.trend for p in performances]
        if "declining" in trends:
            return "declining"
        elif all(t == "improving" for t in trends):
            return "improving"
        else:
            return "stable"

    def _get_agent_traces(self, agent: str) -> list[dict]:
        """获取 Agent 的调用记录。"""
        try:
            import sqlmodel

            from opennovel.schemas.metrics import AgentTrace
            with sqlmodel.Session(self._metrics._engine) as session:
                stmt = sqlmodel.select(AgentTrace).where(
                    AgentTrace.agent == agent
                ).limit(100)
                results = session.exec(stmt).all()
                return [
                    {"duration_ms": r.duration_ms or 0}
                    for r in results
                ]
        except ImportError:
            logger.warning("sqlmodel 未安装，无法读取 Agent 调用记录")
            return []
        except Exception as e:
            logger.warning("读取 Agent %s 的调用记录失败: %s", agent, e)
            return []

    def _get_evaluation_scores(self) -> list[float]:
        """获取全局评分历史（EvaluationHistory 不区分 Agent，所有 Agent 共享同一评分序列）。"""
        try:
            import sqlmodel

            from opennovel.schemas.metrics import EvaluationHistory
            with sqlmodel.Session(self._metrics._engine) as session:
                stmt = sqlmodel.select(EvaluationHistory).order_by(
                    EvaluationHistory.timestamp
                ).limit(100)
                results = session.exec(stmt).all()
                return [r.total_score for r in results if r.total_score is not None]
        except Exception as e:
            logger.warning("无法读取评分历史: %s", e)
            return []
