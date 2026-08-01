"""loom auto 命令 - 三 Agent 自主创作循环。

Writer 思考 → 创作 → Critic 评分 → 不合格重写 → Manager 更新。
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import typer
from rich import print as rprint
from rich.console import Console

auto_app = typer.Typer(help="三 Agent 自主创作循环")
console = Console()


@auto_app.callback(invoke_without_command=True)
def auto(
    path: str = typer.Argument(".", help="项目路径"),
    chapters: int | None = typer.Option(None, "--chapters", "-n", help="覆盖章节数"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只运行 Writer 思考，不实际创作"),
) -> None:
    """三 Agent 自主创作: Writer思考→创作→Critic评分→Manager更新。"""
    from pathlib import Path

    from opennovel.core.auto_runner import AutoRunner
    from opennovel.core.config import LoomConfig

    project_root = Path(path).resolve()

    # 加载配置
    config = LoomConfig.load(project_root)

    if not config.creative_direction:
        rprint("[bold yellow]警告:[/bold yellow] 未设置 creative_direction，Writer 将自由发挥")

    if chapters:
        config.target_chapters = chapters

    # 读取大纲
    outline_path = project_root / config.outline
    if not outline_path.exists():
        rprint(f"[bold red]大纲文件不存在:[/bold red] {outline_path}")
        rprint("请在 outlines/ 目录下创建故事大纲 (Markdown 格式，每章用 ## 标题分隔)")
        raise typer.Exit(1)

    outline_text = outline_path.read_text(encoding="utf-8")
    if not outline_text.strip():
        rprint("[bold red]大纲文件为空[/bold red]")
        raise typer.Exit(1)

    rprint("[bold cyan]OpenNovel Auto[/bold cyan] - 三 Agent 自主创作")
    rprint(f"项目: [bold]{project_root}[/bold]")
    rprint(f"章节数: [dim]{config.target_chapters}[/dim]")
    rprint(f"每章字数: [dim]{config.words_per_chapter}[/dim]")
    rprint(f"创作方向: [dim]{config.creative_direction or '无特殊要求'}[/dim]")
    rprint(f"大纲: [dim]{outline_path}[/dim]\n")

    # 显示 Agent 模型配置
    writer_cfg = config.get_agent_llm_config("writer")
    critic_cfg = config.get_agent_llm_config("critic")
    manager_cfg = config.get_agent_llm_config("manager")
    rprint(f"Writer 模型:  [dim]{writer_cfg['model']}[/dim]")
    rprint(f"Critic 模型:  [dim]{critic_cfg['model']}[/dim]")
    rprint(f"Manager 模型: [dim]{manager_cfg['model']}[/dim]\n")

    if dry_run:
        rprint("[bold yellow]Dry Run 模式[/bold yellow] - 仅测试大纲解析")
        from opennovel.core.auto_runner import parse_outline_from_text
        chapters_parsed = parse_outline_from_text(outline_text)
        for i, (cid, hint) in enumerate(chapters_parsed[: config.target_chapters], 1):
            rprint(f"  {i}. {cid}: {hint[:60]}...")
        rprint(f"\n共 {min(len(chapters_parsed), config.target_chapters)} 章")
        return

    # 执行创作循环
    runner = AutoRunner(project_root, config)
    report = runner.run(outline_text)

    if report.failed_chapters > 0:
        rprint(f"\n[bold yellow]完成，但有 {report.failed_chapters} 章失败[/bold yellow]")
    else:
        rprint(f"\n[bold green]全部完成！[/bold green] 共 {report.successful_chapters} 章")
