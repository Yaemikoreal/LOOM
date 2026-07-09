"""novel causal 命令 - 查询事件因果链。

示例：
    novel causal --event evt_ch001_001
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import typer
from rich import print as rprint
from rich.console import Console

causal_app = typer.Typer(help="查询事件因果链")
console = Console()


@causal_app.callback(invoke_without_command=True)
def causal(
    event: str = typer.Option(..., "--event", "-e", help="起始事件 ID"),
    path: str = typer.Argument(".", help="项目路径"),
    descendants: bool = typer.Option(False, "--descendants", "-d", help="查询因果后继而非前置"),
) -> None:
    """查询指定事件的因果链。"""
    from pathlib import Path

    from opennovel.storage.sqlite import EventStore

    project_root = Path(path).resolve()
    db_path = project_root / ".novel.db"

    if not db_path.exists():
        rprint(f"[bold red]事件账本不存在:[/bold red] {db_path}")
        raise typer.Exit(1)

    with EventStore(db_path) as store:
        if descendants:
            chain = store.get_causal_descendants(event)
            title = f"事件 {event} 的因果后继"
        else:
            chain = store.get_causal_chain(event)
            title = f"事件 {event} 的因果前置链"

    if not chain:
        rprint(f"[yellow]未找到事件 {event} 的因果链记录。[/yellow]")
        return

    rprint(f"[bold cyan]{title}[/bold cyan]\n")
    for evt in chain:
        rprint(
            f"  [{evt.chapter_id}] {evt.event_type}: {evt.description} (压强={evt.causal_pressure})"
        )
