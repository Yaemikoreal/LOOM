"""loom write 命令 - Actor 交互式写作循环。

核心功能：
- 启动交互式写作循环
- Actor 代理根据权威分级上下文续写剧情
- 流式输出纯文本，追加到当前 Markdown 正文区
- 上下文权威分级注入 + Token 熔断
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import typer
from rich import print as rprint
from rich.console import Console

from opennovel.core.llm import LLMBus
from opennovel.core.retriever import Retriever

write_app = typer.Typer(help="Actor 交互式写作循环")
console = Console()


@write_app.callback(invoke_without_command=True)
def write(
    chapter: str = typer.Argument(..., help="章节文件路径（相对于 draft/ 目录）"),
    path: str = typer.Argument(".", help="项目路径"),
    model: str = typer.Option("gpt-4", "--model", "-m", help="LLM 模型名称"),
) -> None:
    """启动 Actor 交互式写作循环。"""
    from pathlib import Path

    from opennovel.agents.actor import Actor
    from opennovel.storage.yaml_storage import YAMLStorage

    project_root = Path(path).resolve()
    chapter_path = project_root / "draft" / chapter

    if not chapter_path.exists():
        rprint(f"[bold red]章节文件不存在:[/bold red] {chapter_path}")
        raise typer.Exit(1)

    rprint("[bold cyan]OpenNovel write[/bold cyan] - 沉浸式续写")
    rprint(f"章节: [bold]{chapter}[/bold]")
    rprint(f"模型: [dim]{model}[/dim]")
    rprint("[dim]输入空行触发续写，输入 :q 退出[/dim]\n")

    # 初始化组件
    from opennovel.core.config import LoomConfig

    config = LoomConfig.load(project_root)
    llm_bus = LLMBus(
        model=model or config.model,
        api_base=config.api_base,
        api_key=config.api_key,
    )
    retriever = Retriever(project_root)
    actor = Actor(llm_bus=llm_bus, retriever=retriever, project_root=project_root)
    storage = YAMLStorage()

    # 读取当前正文
    current_text = storage.read_body(chapter_path)

    while True:
        try:
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            rprint("\n[dim]退出写作模式[/dim]")
            break

        if user_input == ":q":
            rprint("[dim]退出写作模式[/dim]")
            break

        if user_input == "":
            # 触发续写
            rprint("[dim]Actor 正在续写...[/dim]")
            try:
                generated = actor.write_sync(chapter_path, current_text)
                if generated:
                    rprint(f"\n{generated}\n")
                    current_text += "\n" + generated
                else:
                    rprint("[yellow]Actor 未生成内容[/yellow]")
            except Exception as e:
                rprint(f"[bold red]续写出错:[/bold red] {e}")
