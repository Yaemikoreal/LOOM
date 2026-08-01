"""loom commit / rollback 命令 - 状态审阅与固化。

核心功能：
- loom commit: 提取状态并固化（强制 Diff Review + 前置快照）
- 审阅流程：快照生成 → Auditor 提取 → Diff 展示 → 人工确认 → 写入固化

铁律 3：AI 只能提议状态变更，人类拥有绝对否决权。
铁律 4：任何破坏性写入前必须生成 Snapshot。
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from opennovel.core.llm import LLMBus
from opennovel.core.state_manager import StateManager
from opennovel.storage.summaries import write_summary
from opennovel.storage.timeline import write_timeline

commit_app = typer.Typer(help="状态审阅与固化")
console = Console()


@commit_app.callback(invoke_without_command=True)
def commit(
    chapter: str = typer.Argument(..., help="章节文件路径（相对于 draft/ 目录）"),
    path: str = typer.Argument(".", help="项目路径"),
    model: str = typer.Option("gpt-4", "--model", "-m", help="LLM 模型名称"),
) -> None:
    """提取章节状态并固化，强制 Diff Review + 前置快照。"""
    from pathlib import Path

    from opennovel.agents.auditor import Auditor, AuditorAbortError
    from opennovel.storage.yaml_storage import YAMLStorage

    project_root = Path(path).resolve()
    chapter_path = project_root / "draft" / chapter

    if not chapter_path.exists():
        rprint(f"[bold red]章节文件不存在:[/bold red] {chapter_path}")
        raise typer.Exit(1)

    rprint("[bold cyan]OpenNovel commit[/bold cyan] - 状态审阅与固化")
    rprint(f"章节: [bold]{chapter}[/bold]\n")

    # 初始化存储
    storage = YAMLStorage()

    # Step 1: 生成快照（铁律 4）
    rprint("[bold]Step 1/5[/bold] 生成快照...")
    manager = StateManager(project_root)
    chapter_meta, body = storage.read_markdown_file(chapter_path)
    chapter_id = chapter_meta.get("id", chapter.replace(".md", ""))

    # 确定受影响的文件（章节 + 活跃角色）
    active_chars = storage.extract_active_characters(chapter_path)
    affected_files = [chapter_path]
    for char_id in active_chars:
        char_path = project_root / "characters" / f"{char_id}.md"
        if char_path.exists():
            affected_files.append(char_path)

    snapshot = manager.create_snapshot(chapter_id, affected_files=affected_files)
    rprint(f"  [green]✓[/green] 快照已创建: {snapshot.snapshot_id}\n")

    # Step 2: Auditor 提取事件
    rprint("[bold]Step 2/5[/bold] Auditor 提取事件...")
    from opennovel.core.config import LoomConfig

    config = LoomConfig.load(project_root)
    llm_bus = LLMBus(
        model=model or config.model,
        api_base=config.api_base,
        api_key=config.api_key,
    )
    auditor = Auditor(
        llm_bus=llm_bus,
        state_manager=manager,
        project_root=project_root,
        yaml_storage=storage,
    )

    try:
        result = auditor.extract_events_with_retry(chapter_id, body, active_chars)
    except AuditorAbortError:
        rprint("[yellow]用户终止 commit[/yellow]")
        return

    if result.dirty:
        rprint("[bold yellow]脏提交：Auditor 提取失败，状态可能不一致[/bold yellow]")
        return

    if not result.events:
        rprint("[yellow]未检测到状态变更事件[/yellow]")
        return

    events = result.events
    rprint(f"  [green]✓[/green] 提取到 {len(events)} 个事件\n")

    # Step 3: Diff 展示（铁律 3）
    rprint("[bold]Step 3/5[/bold] 变更预览:")
    diffs = auditor.generate_diffs(events)
    diff_text = manager.generate_diff_text(diffs)

    diff_panel = Panel(
        Text(diff_text, style="bold"),
        title="状态变更 Diff",
        border_style="cyan",
    )
    console.print(diff_panel)

    # Step 4: 人工守门（铁律 3）— 逐事件确认
    rprint("\n[bold]Step 4/5[/bold] 人工审阅（逐事件确认）")
    confirmed_events = []

    for i, evt in enumerate(events, 1):
        # 展示事件摘要
        rprint(
            f"\n事件 [bold]{i}/{len(events)}[/bold]: "
            f"[cyan]{evt.event_type}[/cyan] - {evt.description[:80]}"
        )
        rprint(f"  角色: {evt.character_id}  |  因果压强: {evt.causal_pressure}")

        # 逐事件选择
        choice = typer.prompt("  应用此事件? [y/n/detail]", default="y")

        if choice.lower() == "detail":
            if evt.caused_by:
                rprint(f"  前置事件: {evt.caused_by}")
            if evt.related_event_ids:
                rprint(f"  关联事件: {evt.related_event_ids}")
            choice = typer.prompt("  应用此事件? [y/n]", default="y")

        if choice.lower() == "y":
            confirmed_events.append(evt)
            rprint("  [green]✓ 已确认[/green]")
        else:
            rprint("  [yellow]— 已跳过[/yellow]")

    if not confirmed_events:
        rprint("[yellow]未确认任何事件，状态未变更[/yellow]")
        return

    rprint(f"\n[bold]确认: {len(confirmed_events)}/{len(events)} 个事件将写入[/bold]")

    # Step 5: 写入固化
    rprint("\n[bold]Step 5/5[/bold] 写入固化...")
    event_ids = auditor.apply_confirmed_events(confirmed_events, chapter_id)

    # 更新快照的 after 状态（只更新受影响的文件）
    manager.update_snapshot_after(snapshot.snapshot_id, affected_files, event_ids)

    rprint(f"[bold green]✓ 已固化 {len(event_ids)} 个事件[/bold green]")

    # 生成章节摘要（使用已确认的事件数据）
    try:
        events_summary = "; ".join(
            [f"({e.event_type}) {e.description[:60]}" for e in confirmed_events[:5]]
        )
        write_summary(
            project_root=project_root,
            chapter_id=chapter_id,
            chapter_title=chapter_meta.get("title", chapter_id),
            chapter_summary=f"章节 {chapter_id} 已人工审阅并固化。\n\n关键事件:\n{events_summary}",
            key_events=[f"[{e.event_type}] {e.description}" for e in confirmed_events[:10]],
        )
        rprint(f"  [green]✓[/green] 摘要已生成: summaries/{chapter_id}.md")
    except Exception as e:
        rprint(f"  [yellow]摘要写入失败: {e}[/yellow]")

    # 更新时间线
    try:
        write_timeline(project_root)
        rprint(f"  [green]✓[/green] 时间线已更新")
    except Exception as e:
        rprint(f"  [yellow]时间线写入失败: {e}[/yellow]")
