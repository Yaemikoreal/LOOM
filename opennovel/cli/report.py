"""novel report 命令 - 生成项目运行报告。

当前支持：
- `novel report --cost`: 按 agent/model/call_type 输出 Token 消耗与估算成本
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import typer
from rich import print as rprint
from rich.console import Console

report_app = typer.Typer(help="生成项目运行报告")
console = Console()


@report_app.callback(invoke_without_command=True)
def report(
    path: str = typer.Argument(".", help="项目路径"),
    cost: bool = typer.Option(False, "--cost", help="输出 Token 与成本统计"),
) -> None:
    """生成项目运行报告。"""
    from pathlib import Path

    from opennovel.storage.metrics import MetricsStore

    project_root = Path(path).resolve()
    metrics_path = project_root / ".novel.db"

    if not metrics_path.exists():
        rprint(f"[bold yellow]未找到指标数据库:[/bold yellow] {metrics_path}")
        rprint("请先运行 novel auto 生成数据。")
        raise typer.Exit(1)

    if cost:
        with MetricsStore(metrics_path) as store:
            report_data = store.get_cost_report()

        if not report_data["lines"]:
            rprint("[yellow]暂无 Token 消耗记录。[/yellow]")
            return

        rprint(f"[bold cyan]OpenNovel 成本报告[/bold cyan] - {project_root.name}\n")
        rprint(
            f"{'Agent':<12} {'Model':<35} {'Type':<10} {'Calls':>6} "
            f"{'Prompt':>10} {'Completion':>10} {'Cost(USD)':>12}"
        )
        rprint("-" * 95)
        for line in report_data["lines"]:
            rprint(
                f"{line['agent']:<12} {line['model']:<35} {line['call_type']:<10} "
                f"{line['calls']:>6} {line['prompt_tokens']:>10} "
                f"{line['completion_tokens']:>10} {line['cost']:>12.4f}"
            )
        rprint("-" * 95)
        rprint(f"[bold]总估算成本: ${report_data['total_cost']:.4f} USD[/bold]")
        rprint("\n[dim]注：成本按默认单价估算，实际价格以 API 账单为准。[/dim]")
        return

    rprint("[yellow]请使用 --cost 等选项指定报告类型。[/yellow]")
