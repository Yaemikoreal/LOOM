"""novel reindex 命令 — 全量重建搜索索引。

重建所有搜索索引（FTS5 + VectorStore），确保搜索数据一致性。

触发场景:
- 首次构建索引（新项目）
- 长时间未重建（7 天或 5+ 章后提示）
- 用户主动要求重建

执行流程:
1. 扫描所有项目文件（canon/characters/draft/subconscious/foreshadowing）
2. 使用 MarkdownChunker 分块
3. 重建 FTS5 索引（clear → batch add）
4. 重建 VectorStore 索引（canon + subconscious）
5. 输出统计摘要

使用方式:
    novel reindex                           # 重新索引当前目录项目
    novel reindex --path novels/my_story    # 指定项目路径
    novel reindex --force                   # 强制重建（跳过确认）
"""

import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # noqa: E402

import typer

logger = logging.getLogger(__name__)
from rich import print as rprint
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

_console = Console()

from opennovel.core.chunker import MarkdownChunker
from opennovel.schemas.search import Chunk, ChunkSource
from opennovel.storage.fts5 import Fts5Store

# 需要建立索引的目录及其对应的 ChunkSource
INDEX_SOURCES: dict[str, ChunkSource] = {
    "canon": ChunkSource.CANON,
    "characters": ChunkSource.CHARACTER,
    "draft": ChunkSource.DRAFT,
    "subconscious": ChunkSource.SUBCONSCIOUS,
    "foreshadowing": ChunkSource.CANON,
}

DEFAULT_EXTS = {".md", ".txt"}


def run_reindex(project_root: Path, force: bool = False) -> bool:
    """执行全量重建索引。

    Args:
        project_root: 项目根目录路径
        force: 是否强制重建

    Returns:
        重建是否成功
    """
    rprint("[bold cyan]novel reindex[/bold cyan] - 全量重建搜索索引")
    rprint(f"项目: [bold]{project_root}[/bold]\n")

    if not (project_root / "novel.yaml").exists():
        rprint("[red]✗ 项目未初始化（novel.yaml 不存在）[/red]")
        return False

    if not force:
        rprint("[yellow]此操作将重建所有搜索索引（FTS5 + 向量），可能需要 30s 以上。[/yellow]")
        confirm = typer.confirm("确认继续?", default=True)
        if not confirm:
            rprint("[dim]已取消[/dim]")
            return False

    # ── Step 1: 扫描文件 ──
    rprint("\n[bold]Step 1/4:[/bold] 扫描项目文件...")
    files_by_source: dict[ChunkSource, list[Path]] = {src: [] for src in INDEX_SOURCES.values()}
    total_files = 0

    for dir_name, source in INDEX_SOURCES.items():
        dir_path = project_root / dir_name
        if not dir_path.exists():
            continue
        files = sorted(f for f in dir_path.rglob("*") if f.suffix in DEFAULT_EXTS and f.is_file())
        files_by_source[source] = files
        total_files += len(files)
        rprint(f"  [dim]{dir_name}/:[/dim] {len(files)} files")

    if total_files == 0:
        rprint("[yellow]未找到可索引的文件[/yellow]")
        return False

    rprint(f"  [green]✓[/green] 共扫描到 {total_files} 个文件\n")

    # ── Step 2: 分块 ──
    rprint("[bold]Step 2/4:[/bold] Markdown 递归分块...")
    chunker = MarkdownChunker()
    all_chunks: list[Chunk] = []

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=_console,
    ) as progress:
        task = progress.add_task("分块中...", total=total_files)
        for files in files_by_source.values():
            for file_path in files:
                parent_dir = file_path.relative_to(project_root).parts[0]
                source = INDEX_SOURCES.get(parent_dir, ChunkSource.CANON)
                metadata = {"file_path": str(file_path.relative_to(project_root))}

                chunks = chunker.chunk_file(file_path, source, metadata)
                all_chunks.extend(chunks)
                progress.advance(task)

    rprint(f"  [green]✓[/green] 生成 {len(all_chunks)} 个分块\n")

    # ── Step 3: 重建 FTS5 ──
    rprint("[bold]Step 3/4:[/bold] 重建 FTS5 索引...")
    try:
        fts5 = Fts5Store(project_root)
        fts5.clear_all()
        added = fts5.add_chunks_batch(all_chunks)
        fts5.update_rebuild_meta(added)
        rprint(f"  [green]✓[/green] FTS5 索引已重建 ({added} 分块)\n")
    except Exception as e:
        rprint(f"  [red]✗ FTS5 重建失败: {e}[/red]")
        return False

    # ── Step 4: 重建 VectorStore ──
    rprint("[bold]Step 4/4:[/bold] 重建向量索引...")
    try:
        from opennovel.core.retriever import Retriever

        retriever = Retriever(project_root)
        retriever.build_canon_index()
        retriever.build_subconscious_index()
        rprint("  [green]✓[/green] 向量索引已重建\n")
    except Exception as e:
        rprint(f"  [yellow]⚠ 向量索引重建失败: {e}[/yellow]")
        rprint("  [dim]向量索引为可选功能，不影响文本搜索。安装后可启用。[/dim]")

    # ── 统计摘要 ──
    rprint("[bold]━━━ 索引重建完成 ━━━[/bold]")

    try:
        total = fts5.get_total_chunk_count()
        info = fts5.get_rebuild_info()
        table = Table()
        table.add_column("指标", style="cyan")
        table.add_column("数值")
        table.add_row("总分块数", str(total))
        table.add_row("索引版本", info.get("index_version", "1"))
        table.add_row("重建时间", info.get("rebuilt_at", "now"))
        table.add_row("项目路径", str(project_root))
        typer.echo()
        typer.echo(table)
    except Exception as e:
        logger.debug("统计摘要渲染失败: %s", e)

    return True


reindex_app = typer.Typer(help="全量重建搜索索引")


@reindex_app.callback(invoke_without_command=True)
def reindex(
    path: str = typer.Argument(".", help="项目路径"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重建，跳过确认"),
) -> None:
    """全量重建搜索索引（FTS5 + 向量索引）。

    扫描项目所有目录（canon/characters/draft/subconscious/foreshadowing），
    按 Markdown 结构递归分块后重建 FTS5 和向量索引。
    """
    project_root = Path(path).resolve()
    result = run_reindex(project_root, force=force)
    if not result:
        raise typer.Exit(1)
