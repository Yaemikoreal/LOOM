"""评分审计器 — 分析 Critic 评分的一致性和偏差。

利用 MetricsStore 的 EvaluationHistory 数据进行统计分析，
帮助判断 Critic 是否存在系统性评分偏差。

使用方式:
    auditor = EvaluationAuditor(metrics_store)
    report = auditor.analyze()
    for dim, stats in report["dimensions"].items():
        print(f"{dim}: 均值={stats['mean']}, 标准差={stats['std']}")

设计原则：
- 纯统计分析，不修改 Critic 行为
- 依赖 MetricsStore（可选），metrics.db 不存在时返回空报告
"""

import logging
import statistics
from pathlib import Path

from opennovel.storage.metrics import MetricsStore

logger = logging.getLogger(__name__)


class EvaluationAuditor:
    """评分审计器：分析 Critic 评分的一致性和偏差。"""

    def __init__(self, metrics_store: MetricsStore) -> None:
        """初始化审计器。

        Args:
            metrics_store: MetricsStore 实例
        """
        self._metrics = metrics_store

    @classmethod
    def from_project(cls, project_root: Path) -> "EvaluationAuditor | None":
        """从项目路径创建审计器（metrics.db 可选）。

        Args:
            project_root: 项目根目录

        Returns:
            EvaluationAuditor 实例，或无数据时返回 None
        """
        metrics_path = project_root / ".novel.metrics.db"
        if not metrics_path.exists():
            return None
        try:
            store = MetricsStore(metrics_path)
            return cls(store)
        except Exception:
            return None

    def analyze(self) -> dict:
        """对所有历史评分进行统计分析。

        Returns:
            包含各维度统计数据和告警的报告字典
        """
        history = self._metrics.get_evaluation_history()
        if not history:
            return {"total_evaluations": 0, "dimensions": {}, "alerts": [], "message": "暂无评分数据"}

        dims: dict[str, list[int]] = {
            "文笔质量": [],
            "情节逻辑": [],
            "角色一致": [],
            "节奏把控": [],
            "情感表达": [],
        }

        # 映射 schema 字段名到显示名
        field_map = {
            "文笔质量": "dimension_writing",
            "情节逻辑": "dimension_plot",
            "角色一致": "dimension_character",
            "节奏把控": "dimension_rhythm",
            "情感表达": "dimension_emotion",
        }

        for record in history:
            for display_name, field in field_map.items():
                value = getattr(record, field, 0)
                dims[display_name].append(value)

        report: dict = {
            "total_evaluations": len(history),
            "total_score_avg": round(
                sum(r.total_score for r in history) / len(history), 1
            ),
            "dimensions": {},
            "alerts": [],
        }

        for name, scores in dims.items():
            if not scores:
                continue
            stats_data = {
                "mean": round(statistics.mean(scores), 1),
                "std": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0,
                "min": min(scores),
                "max": max(scores),
                "range": max(scores) - min(scores),
            }
            report["dimensions"][name] = stats_data

            # 检测异常
            if stats_data["std"] > 2.0:
                report["alerts"].append(
                    f"{name} 标准差 {stats_data['std']} 偏高（>2.0），"
                    "可能存在评分漂移"
                )

        # 检测整体趋势
        scores = [r.total_score for r in history]
        if len(scores) >= 4:
            first_half = sum(scores[: len(scores) // 2]) / (len(scores) // 2)
            second_half = sum(scores[-(len(scores) // 2) :]) / (len(scores) // 2)
            if abs(second_half - first_half) > 10:
                direction = "上升" if second_half > first_half else "下降"
                report["alerts"].append(
                    f"评分趋势{direction}: 后半段均值 {second_half}，前半段 {first_half}，"
                    f"差异 {abs(second_half - first_half):.1f} 分"
                )

        # 检测评分集中（区分度不足）
        if len(scores) >= 3:
            score_std = statistics.stdev(scores)
            if score_std < 3.0:
                report["alerts"].append(
                    f"评分标准差 {score_std:.2f} 偏低（<3.0），"
                    "所有章节评分过于集中，可能缺乏区分度"
                )

        return report

    def format_report(self, report: dict) -> str:
        """将分析报告格式化为可读文本。

        Args:
            report: analyze() 返回的报告

        Returns:
            格式化的报告文本
        """
        lines = ["Critic 评分校准报告"]

        if report.get("total_evaluations", 0) == 0:
            lines.append("暂无评分数据。运行 novel auto 后再次检查。")
            return "\n".join(lines)

        lines.append(f"共 {report['total_evaluations']} 次评分")
        lines.append(f"总分均值: {report.get('total_score_avg', '?')}")
        lines.append("")

        # 各维度
        lines.append("各维度统计:")
        for name, stats_data in report.get("dimensions", {}).items():
            marker = " ⚠" if stats_data["std"] > 2.0 else ""
            lines.append(
                f"  {name}: 均值 {stats_data['mean']}/20  "
                f"标准差 {stats_data['std']}  范围 {stats_data['min']}-{stats_data['max']}{marker}"
            )

        # 告警
        alerts = report.get("alerts", [])
        if alerts:
            lines.append("")
            lines.append("告警:")
            for alert in alerts:
                lines.append(f"  ⚠ {alert}")

        return "\n".join(lines)
