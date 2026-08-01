"""loom stash 命令 - 灵感潜意识池。

核心功能：
- 随时收录零散灵感（台词、画面、象征物）
- 追加至 subconscious/lines.md
- 触发后台增量向量化索引
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import typer
from rich import print as rprint

stash_app = typer.Typer(help="灵感潜意识池")


@stash_app.callback(invoke_without_command=True)
def stash(
    text: str = typer.Argument(..., help="灵感文本内容"),
    tags: list[str] = typer.Option([], "--tag", "-t", help="标签，可多次使用"),
    path: str = typer.Argument(".", help="项目路径"),
) -> None:
    """存入灵感到潜意识池，自动增量索引。"""
    from pathlib import Path

    from opennovel.core.retriever import Retriever

    project_root = Path(path).resolve()

    # 检查项目是否已初始化
    sub_dir = project_root / "subconscious"
    if not sub_dir.exists():
        rprint("[bold red]项目未初始化！[/bold red] 请先运行 [bold]loom init[/bold]")
        raise typer.Exit(1)

    rprint("[bold cyan]OpenNovel stash[/bold cyan] - 灵感潜意识池")

    # 写入灵感
    retriever = Retriever(project_root)
    retriever.add_to_subconscious(text, tags)

    tag_display = " ".join(f"[dim]#{t}[/dim]" for t in tags)
    rprint(f"  [green]✓[/green] 已存入: {text[:50]}... {tag_display}")
