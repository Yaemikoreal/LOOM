"""检索性能 Benchmark 脚本 — 200 万字长篇小说场景。

使用合成数据测量 OpenNovel 三通道混合检索延迟：
- 向量语义检索（CANON / SUBCONSCIOUS）
- FTS5 关键词检索（DRAFT）
- EventStore 事件检索
- RRF 融合
- Cross-Encoder 重排序

用法:
    python scripts/benchmark_retrieval.py --novel novels/demo_novel --chapters 20
    python scripts/benchmark_retrieval.py --novel novels/demo_novel --chapters 100 \
        --words-per-chapter 2000 --queries 10 --output benchmark.json --overwrite

注意:
    本脚本会在指定项目下生成合成数据（draft/、canon/benchmark_*.md、
    subconscious/benchmark_*.md）并向 EventStore 写入事件。如担心覆盖真实
    项目数据，请先在临时目录执行 `novel init .` 后再指定该项目。
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 支持直接 `python scripts/benchmark_retrieval.py` 运行：将项目根目录加入路径
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 项目依赖
from opennovel.core.retriever import Retriever
from opennovel.core.search_pipeline import SearchPipeline
from opennovel.schemas.event import EventCreate, EventType
from opennovel.storage.fts5 import Fts5Store
from opennovel.storage.sqlite import EventStore

# 可选依赖：psutil 用于内存采样，缺失时使用 tracemalloc 估算
try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

# 可选依赖：Rich 用于终端表格，缺失时回退到纯文本
try:
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover
    Console = None  # type: ignore[misc, assignment]
    Table = None  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 合成数据配置
# ──────────────────────────────────────────────────────────────────────────────

CHARACTERS: list[tuple[str, str]] = [
    ("char_ailin", "艾琳"),
    ("char_kaer", "卡尔"),
    ("char_morwen", "莫雯"),
    ("char_lucian", "卢锡安"),
]

LOCATIONS: list[str] = [
    "影渊森林",
    "星辉城",
    "暗夜神殿",
    "霜风峡谷",
    "遗忘废墟",
]

ARTIFACTS: list[str] = [
    "古代符文",
    "星辰碎片",
    "暗影之冠",
    "时光沙漏",
]

EVENT_TEMPLATES: list[str] = [
    "{char} 在 {loc} 发现了 {art}，这似乎与古老的预言有关。",
    "{char2} 暗中跟踪 {char}，试图在 {loc} 夺取 {art}。",
    "{char} 与 {char2} 在 {loc} 因 {art} 爆发激烈冲突。",
    "为了守护 {art}，{char} 决定深入 {loc} 的核心区域。",
    "{loc} 的天空突然变暗，{char} 感到 {art} 正在释放能量。",
]

FILLER_PARAGRAPHS: list[str] = [
    "风吹过荒芜的大地，卷起阵阵尘土。远处的山峦在暮色中若隐若现。",
    "时间仿佛在这一刻凝固，所有的声音都被黑暗吞没。",
    "心中的疑虑如同藤蔓般蔓延，但脚步却无法停下。",
    "古老的石碑上刻满了无人能解的符号，诉说着被遗忘的历史。",
    "星光透过云层洒落，为这片神秘的土地镀上一层银色。",
    "空气中弥漫着紧张的气息，仿佛下一刻就会有变故发生。",
    "回忆如潮水般涌来，却又在触及真相前悄然退去。",
    "这是一场没有退路的旅程，每一步都写满了未知与危险。",
]

# 用于 Benchmark 的固定查询，覆盖角色、地点、物品等可检索实体
BENCHMARK_QUERIES: list[str] = [
    "艾琳 影渊森林 古代符文",
    "卡尔 星辉城 星辰碎片",
    "莫雯 暗夜神殿 暗影之冠",
    "卢锡安 霜风峡谷 时光沙漏",
    "影渊森林 发现 古代符文",
    "星辉城 冲突 星辰碎片",
    "暗夜神殿 能量 暗影之冠",
    "霜风峡谷 旅程 时光沙漏",
    "遗忘废墟 石碑 历史",
    "古代符文 预言 真相",
]


# ──────────────────────────────────────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class BenchmarkResult:
    """Benchmark 结果数据结构。"""

    config: dict[str, Any] = field(default_factory=dict)
    data_generation: dict[str, Any] = field(default_factory=dict)
    index_build: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化的字典。"""
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "config": self.config,
            "data_generation": self.data_generation,
            "index_build": self.index_build,
            "retrieval": self.retrieval,
            "memory": self.memory,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 合成数据生成器
# ──────────────────────────────────────────────────────────────────────────────


class SyntheticNovelGenerator:
    """合成长篇小说数据生成器。

    在项目目录下生成：
    - draft/ch_001.md ~ ch_N.md
    - canon/benchmark_world.md
    - subconscious/benchmark_notes.md
    - EventStore 事件记录
    """

    def __init__(
        self,
        project_root: Path,
        chapters: int = 20,
        words_per_chapter: int = 2000,
        seed: int = 42,
    ) -> None:
        """初始化生成器。

        Args:
            project_root: 项目根目录
            chapters: 合成章节数
            words_per_chapter: 每章目标中文字符数
            seed: 随机种子，保证可重复
        """
        self.project_root = project_root
        self.chapters = chapters
        self.words_per_chapter = words_per_chapter
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_all(self, overwrite: bool = False) -> dict[str, Any]:
        """生成全部合成数据。

        Args:
            overwrite: 是否覆盖已存在的章节文件

        Returns:
            生成统计信息字典
        """
        draft_dir = self.project_root / "draft"
        draft_dir.mkdir(parents=True, exist_ok=True)

        chapters_written = 0
        total_chars = 0
        chapter_metas: list[dict[str, Any]] = []

        for idx in range(1, self.chapters + 1):
            chapter_file = draft_dir / f"ch_{idx:03d}.md"
            if chapter_file.exists() and not overwrite:
                logger.info("章节已存在，跳过: %s", chapter_file)
                content = chapter_file.read_text(encoding="utf-8")
                meta = self._extract_meta_from_content(content, idx)
                total_chars += self._count_chars(content)
                chapter_metas.append(meta)
                continue

            content, meta = self._generate_chapter(idx)
            chapter_file.write_text(content, encoding="utf-8")
            chapters_written += 1
            total_chars += self._count_chars(content)
            chapter_metas.append(meta)

        canon_file = self._generate_canon()
        sub_file, highlight_count = self._generate_subconscious(chapter_metas)
        event_count = self._generate_events(chapter_metas)

        return {
            "chapters_requested": self.chapters,
            "chapters_written": chapters_written,
            "draft_dir": str(draft_dir),
            "total_chars": total_chars,
            "canon_file": str(canon_file),
            "subconscious_file": str(sub_file),
            "highlight_count": highlight_count,
            "events_count": event_count,
        }

    def _generate_chapter(self, idx: int) -> tuple[str, dict[str, Any]]:
        """生成单个章节内容。

        Args:
            idx: 章节序号（从 1 开始）

        Returns:
            (Markdown 正文, 章节元信息)
        """
        # 使用章节相关种子，保证同一章节每次运行结果一致
        rng = random.Random(self.seed + idx)

        char_id, char_name = rng.choice(CHARACTERS)
        char_id_2, char_name_2 = rng.choice([c for c in CHARACTERS if c[0] != char_id])
        loc = rng.choice(LOCATIONS)
        art = rng.choice(ARTIFACTS)
        event_type = rng.choice(list(EventType))

        lines: list[str] = [
            f"# 第 {idx} 章",
            "",
            f"{char_name} 站在 {loc} 的边缘，目光落在手中的 {art} 上。",
            f"不远处，{char_name_2} 正警惕地注视着四周的动静。",
            "",
        ]

        target = self.words_per_chapter
        current_chars = sum(len(line) for line in lines)

        # 主体段落：在模板与 filler 之间切换，确保实体反复出现
        while current_chars < target:
            if rng.random() < 0.6:
                template = rng.choice(EVENT_TEMPLATES)
                para = template.format(
                    char=char_name,
                    char2=char_name_2,
                    loc=loc,
                    art=art,
                )
            else:
                para = rng.choice(FILLER_PARAGRAPHS)
                # 随机把 filler 与当前实体结合，提高可检索密度
                if rng.random() < 0.3:
                    para = f"{char_name} 心想：{para}"

            lines.append(para)
            lines.append("")
            current_chars += len(para)

        content = "\n".join(lines)
        meta = {
            "chapter_id": f"ch_{idx:03d}",
            "char_id": char_id,
            "char_id_2": char_id_2,
            "location": loc,
            "artifact": art,
            "event_type": event_type,
            "summary": f"{char_name} 在 {loc} 与 {char_name_2} 因 {art} 产生事件",
        }
        return content, meta

    def _generate_canon(self) -> Path:
        """生成世界观设定文件，供向量语义检索使用。"""
        canon_dir = self.project_root / "canon"
        canon_dir.mkdir(parents=True, exist_ok=True)
        canon_file = canon_dir / "benchmark_world.md"

        lines: list[str] = ["# 合成世界观设定\n"]
        for loc in LOCATIONS:
            lines.append(f"## {loc}")
            lines.append(f"{loc} 是这片大陆上充满神秘力量的区域，许多冒险者在此寻找失落的真相。\n")
        for art in ARTIFACTS:
            lines.append(f"## {art}")
            lines.append(f"{art} 拥有改变叙事走向的潜力，仅在关键时刻出现。\n")
        for char_id, char_name in CHARACTERS:
            lines.append(f"## {char_name}")
            lines.append(
                f"{char_name}（{char_id}）是故事中的核心角色，与其他角色和物品存在复杂关联。\n"
            )

        canon_file.write_text("\n".join(lines), encoding="utf-8")
        return canon_file

    def _generate_subconscious(self, chapter_metas: list[dict[str, Any]]) -> tuple[Path, int]:
        """生成潜意识池灵感文件，供向量语义检索使用。

        Args:
            chapter_metas: 各章节元信息

        Returns:
            (文件路径, 灵感条数)
        """
        sub_dir = self.project_root / "subconscious"
        sub_dir.mkdir(parents=True, exist_ok=True)
        sub_file = sub_dir / "benchmark_notes.md"

        highlights: list[str] = []
        for meta in chapter_metas:
            highlights.append(f"- {meta['summary']} #{meta['chapter_id']}")

        sub_file.write_text("\n".join(highlights) + "\n", encoding="utf-8")
        return sub_file, len(highlights)

    def _generate_events(self, chapter_metas: list[dict[str, Any]]) -> int:
        """向 EventStore 批量写入合成事件。

        Args:
            chapter_metas: 各章节元信息

        Returns:
            写入的事件数量
        """
        db_path = self.project_root / ".novel.db"
        events: list[EventCreate] = []
        for meta in chapter_metas:
            event = EventCreate(
                event_id=f"evt_{meta['chapter_id']}_synthetic",
                chapter_id=meta["chapter_id"],
                timestamp=f"day_{int(meta['chapter_id'].split('_')[1])}",
                character_id=meta["char_id"],
                event_type=meta["event_type"],
                description=meta["summary"],
                causal_pressure=round(0.5 + 0.4 * random.random(), 2),
                caused_by=None,
                related_event_ids=None,
            )
            events.append(event)

        with EventStore(db_path) as store:
            store.add_events_batch(events)

        return len(events)

    @staticmethod
    def _count_chars(text: str) -> int:
        """统计中文字符与数字字母总数（近似字数）。"""
        return sum(1 for c in text if c.isalnum() or "\u4e00" <= c <= "\u9fff")

    @staticmethod
    def _extract_meta_from_content(content: str, idx: int) -> dict[str, Any]:
        """从已存在的章节内容中提取简单元信息（用于跳过生成时）。"""
        return {
            "chapter_id": f"ch_{idx:03d}",
            "char_id": "char_ailin",
            "char_id_2": "char_kaer",
            "location": "影渊森林",
            "artifact": "古代符文",
            "event_type": EventType.CUSTOM,
            "summary": f"从已有文件提取的章节 {idx} 摘要",
        }


# ──────────────────────────────────────────────────────────────────────────────
# Benchmark 执行器
# ──────────────────────────────────────────────────────────────────────────────


class RetrievalBenchmark:
    """检索性能测量执行器。"""

    def __init__(
        self,
        project_root: Path,
        chapters: int = 20,
        words_per_chapter: int = 2000,
        queries: int = 10,
    ) -> None:
        """初始化 Benchmark。

        Args:
            project_root: 项目根目录
            chapters: 合成章节数
            words_per_chapter: 每章目标字数
            queries: 查询次数
        """
        self.project_root = project_root
        self.chapters = chapters
        self.words_per_chapter = words_per_chapter
        self.queries = min(queries, len(BENCHMARK_QUERIES))
        self.result = BenchmarkResult()

    def run(self, overwrite: bool = False) -> BenchmarkResult:
        """执行完整 Benchmark 流程。

        Args:
            overwrite: 是否覆盖已有章节文件

        Returns:
            BenchmarkResult 结果对象
        """
        self.result.timestamp = datetime.now(timezone.utc).isoformat()
        self.result.config = {
            "project_root": str(self.project_root),
            "chapters": self.chapters,
            "words_per_chapter": self.words_per_chapter,
            "queries": self.queries,
            "overwrite": overwrite,
        }

        # 1. 生成合成数据
        generator = SyntheticNovelGenerator(
            self.project_root,
            chapters=self.chapters,
            words_per_chapter=self.words_per_chapter,
        )
        self.result.data_generation = generator.generate_all(overwrite=overwrite)

        # 2. 构建索引并测量
        self._build_indexes()

        # 3. 执行检索并测量延迟
        self._measure_retrieval()

        return self.result

    def _build_indexes(self) -> None:
        """构建/刷新 FTS5、向量、事件索引，并记录耗时与内存。"""
        memory_start = self._memory_mb()

        # FTS5 索引：增量更新所有 draft 文件
        fts5_time = self._build_fts5_index()

        # 向量索引：构建 subconscious 索引（包含 highlights）
        vector_time, vector_available = self._build_vector_index()

        # 事件已在前一步批量写入，此处仅记录元信息
        event_time = 0.0

        memory_after = self._memory_mb()
        memory_peak = self._peak_memory_mb()

        self.result.index_build = {
            "fts5_time_ms": round(fts5_time * 1000, 2),
            "vector_time_ms": round(vector_time * 1000, 2),
            "event_time_ms": round(event_time * 1000, 2),
            "total_time_ms": round((fts5_time + vector_time + event_time) * 1000, 2),
            "vector_available": vector_available,
        }
        self.result.memory = {
            "start_mb": memory_start,
            "after_index_mb": memory_after,
            "index_build_peak_mb": memory_peak,
            "peak_mb": memory_peak,
        }

    def _build_fts5_index(self) -> float:
        """构建/刷新 FTS5 索引。

        Returns:
            耗时（秒）
        """
        draft_dir = self.project_root / "draft"
        fts5_store = Fts5Store(self.project_root)

        start = time.perf_counter()
        for chapter_file in sorted(draft_dir.glob("ch_*.md")):
            try:
                fts5_store.incremental_update_file(
                    chapter_file,
                    source="draft",
                    metadata={"doc_stem": chapter_file.stem},
                )
            except Exception as e:  # pragma: no cover
                logger.warning("FTS5 更新失败 %s: %s", chapter_file, e)
        fts5_store.close()

        return time.perf_counter() - start

    def _build_vector_index(self) -> tuple[float, bool]:
        """构建潜意识向量索引。

        Returns:
            (耗时秒数, 向量模型是否可用)
        """
        retriever = Retriever(self.project_root)

        start = time.perf_counter()
        retriever.build_subconscious_index()
        elapsed = time.perf_counter() - start

        # 检查向量模型是否真正可用：尝试一次查询
        available = False
        try:
            test_result = retriever.query_subconscious("古代符文", top_k=1)
            available = test_result != ""
        except Exception:  # pragma: no cover
            available = False

        return elapsed, available

    def _measure_retrieval(self) -> None:
        """测量串行/并行检索与 Cross-Encoder 延迟。"""
        event_store = EventStore(self.project_root / ".novel.db")
        fts5_store = Fts5Store(self.project_root)
        retriever = Retriever(self.project_root)
        # 确保向量索引已加载
        retriever.build_subconscious_index()

        pipeline = SearchPipeline(
            project_root=self.project_root,
            event_store=event_store,
            retriever=retriever,
            fts5_store=fts5_store,
        )

        queries = BENCHMARK_QUERIES[: self.queries]
        serial_times: list[float] = []
        parallel_times: list[float] = []
        reranker_times: list[float] = []

        for query in queries:
            chapter_id = f"ch_{(hash(query) % self.chapters) + 1:03d}"

            # 串行三通道检索
            t0 = time.perf_counter()
            self._search_serial(pipeline, query, chapter_id)
            serial_times.append(time.perf_counter() - t0)

            # 并行三通道检索（不带 reranker）。
            # 注意：当前 SearchPipeline 内部使用 ThreadPoolExecutor，而 Fts5Store 的
            # SQLite 连接非线程安全，可能在 worker 线程中触发警告；benchmark 会如实记录。
            t0 = time.perf_counter()
            pipeline.search(
                query,
                chapter_id=chapter_id,
                use_reranker=False,
                top_k=5,
            )
            parallel_times.append(time.perf_counter() - t0)

            # Cross-Encoder 重排序延迟（独立测量）
            reranker_times.append(self._measure_reranker_latency(pipeline, query, chapter_id))

        event_store.close()
        fts5_store.close()

        avg_serial = sum(serial_times) / len(serial_times) if serial_times else 0.0
        avg_parallel = sum(parallel_times) / len(parallel_times) if parallel_times else 0.0
        avg_reranker = sum(reranker_times) / len(reranker_times) if reranker_times else 0.0
        avg_query_latency = (
            (sum(parallel_times) + sum(reranker_times)) / len(queries) if queries else 0.0
        )

        speedup = avg_serial / avg_parallel if avg_parallel > 0 else 0.0

        self.result.retrieval = {
            "serial_time_ms": round(avg_serial * 1000, 2),
            "parallel_time_ms": round(avg_parallel * 1000, 2),
            "speedup": round(speedup, 2),
            "reranker_time_ms": round(avg_reranker * 1000, 2),
            "avg_query_latency_ms": round(avg_query_latency * 1000, 2),
            "queries_executed": len(queries),
            "vector_available": self.result.index_build.get("vector_available", False),
            "reranker_available": pipeline.reranker.is_available,
        }

        # 更新峰值内存
        peak = self._peak_memory_mb()
        if peak is not None and (
            self.result.memory.get("peak_mb") is None
            or (
                self.result.memory.get("peak_mb") is not None
                and peak > self.result.memory["peak_mb"]
            )
        ):
            self.result.memory["peak_mb"] = peak

    @staticmethod
    def _search_serial(
        pipeline: SearchPipeline,
        query: str,
        chapter_id: str,
    ) -> None:
        """串行执行三通道检索（用于与并行模式对比）。

        直接调用 SearchPipeline 的三个私有检索方法，避免线程池开销。
        """
        pipeline._search_vector(query, top_k=15)
        pipeline._search_fts5(query, top_k=15)
        pipeline._search_events(
            query,
            chapter_id=chapter_id,
            character_ids=None,
            top_k=15,
        )

    def _measure_reranker_latency(
        self,
        pipeline: SearchPipeline,
        query: str,
        chapter_id: str,
    ) -> float:
        """独立测量 Cross-Encoder 重排序延迟。

        先通过无 reranker 的搜索获取候选，再对候选执行 rerank。
        若 reranker 不可用，返回 0.0 并在结果中标记。

        Returns:
            单次 rerank 耗时（秒）
        """
        if not pipeline.reranker.is_available:
            return 0.0

        # 获取候选文本
        result = pipeline.search(
            query,
            chapter_id=chapter_id,
            use_reranker=False,
            top_k=50,
        )
        candidates = [rc.chunk.text for rc in result.chunks[:50]]
        if len(candidates) < 2:
            return 0.0

        start = time.perf_counter()
        try:
            pipeline.reranker.rerank(query, candidates, threshold_exit=False)
        except Exception as e:  # pragma: no cover
            logger.warning("Reranker 测量失败: %s", e)
            return 0.0
        return time.perf_counter() - start

    # ── 内存采样辅助 ─────────────────────────────────────────────────────────

    @staticmethod
    def _memory_mb() -> float | None:
        """获取当前进程 RSS 内存（MB），psutil 不可用时返回 None。"""
        if psutil is None:
            return None
        try:
            process = psutil.Process()
            return round(process.memory_info().rss / (1024 * 1024), 2)
        except Exception:  # pragma: no cover
            return None

    @staticmethod
    def _peak_memory_mb() -> float | None:
        """获取当前内存峰值（MB）。

        优先使用 psutil 采样；若 psutil 不可用，使用 tracemalloc 峰值估算。
        """
        if psutil is not None:
            try:
                process = psutil.Process()
                return round(process.memory_info().rss / (1024 * 1024), 2)
            except Exception:  # pragma: no cover
                pass

        # tracemalloc 回退
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        _, peak = tracemalloc.get_traced_memory()
        return round(peak / (1024 * 1024), 2)


# ──────────────────────────────────────────────────────────────────────────────
# 输出与 CLI
# ──────────────────────────────────────────────────────────────────────────────


def print_results(result: BenchmarkResult) -> None:
    """在终端以表格形式输出结果。"""
    data = result.to_dict()

    if Console is not None and Table is not None:
        console = Console()
        console.print("\n[bold cyan]OpenNovel 检索性能 Benchmark[/bold cyan]\n")

        table = Table(title="数据生成")
        table.add_column("指标", style="green")
        table.add_column("数值", style="yellow")
        dg = data["data_generation"]
        table.add_row("请求章节数", str(dg["chapters_requested"]))
        table.add_row("实际写入章节数", str(dg["chapters_written"]))
        table.add_row("总字符数", f"{dg['total_chars']:,}")
        table.add_row("潜意识灵感数", str(dg["highlight_count"]))
        table.add_row("事件数", str(dg["events_count"]))
        console.print(table)

        table = Table(title="索引构建")
        table.add_column("指标", style="green")
        table.add_column("耗时 (ms)", style="yellow")
        ib = data["index_build"]
        table.add_row("FTS5 索引", f"{ib['fts5_time_ms']:.2f}")
        table.add_row("向量索引", f"{ib['vector_time_ms']:.2f}")
        table.add_row("事件索引", f"{ib['event_time_ms']:.2f}")
        table.add_row("总计", f"{ib['total_time_ms']:.2f}")
        table.add_row("向量模型可用", str(ib["vector_available"]))
        console.print(table)

        table = Table(title="检索延迟")
        table.add_column("指标", style="green")
        table.add_column("耗时 (ms)", style="yellow")
        r = data["retrieval"]
        table.add_row("串行三通道检索", f"{r['serial_time_ms']:.2f}")
        table.add_row("并行三通道检索", f"{r['parallel_time_ms']:.2f}")
        table.add_row("并行加速比", f"{r['speedup']:.2f}x")
        table.add_row("Cross-Encoder 重排序", f"{r['reranker_time_ms']:.2f}")
        table.add_row("单次查询平均延迟", f"{r['avg_query_latency_ms']:.2f}")
        table.add_row("执行查询数", str(r["queries_executed"]))
        table.add_row("Reranker 可用", str(r["reranker_available"]))
        console.print(table)

        table = Table(title="内存占用")
        table.add_column("指标", style="green")
        table.add_column("数值 (MB)", style="yellow")
        m = data["memory"]
        table.add_row("初始 RSS", _fmt_mb(m.get("start_mb")))
        table.add_row("索引构建后 RSS", _fmt_mb(m.get("after_index_mb")))
        table.add_row("峰值 RSS", _fmt_mb(m.get("peak_mb")))
        console.print(table)
    else:
        # Rich 不可用时回退到 JSON 输出
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _fmt_mb(value: float | None) -> str:
    """格式化内存值为字符串。"""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="OpenNovel 三通道检索性能 Benchmark（合成数据）",
    )
    parser.add_argument(
        "--novel",
        type=Path,
        default=Path("novels/demo_novel"),
        help="项目路径（默认: novels/demo_novel）",
    )
    parser.add_argument(
        "--chapters",
        type=int,
        default=20,
        help="合成章节数（默认: 20）",
    )
    parser.add_argument(
        "--words-per-chapter",
        type=int,
        default=2000,
        help="每章目标字数（默认: 2000）",
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=10,
        help="查询次数（默认: 10）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="结果 JSON 文件路径（可选）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的章节文件",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出详细日志",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Benchmark 入口函数。

    Args:
        argv: 命令行参数列表，None 时使用 sys.argv

    Returns:
        退出码，0 表示成功
    """
    args = parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    project_root = args.novel.resolve()
    if not project_root.exists():
        logger.error("项目路径不存在: %s", project_root)
        return 1

    # 确保项目存在 novel.yaml（不存在时创建最小配置，避免配置加载失败）
    config_path = project_root / "novel.yaml"
    if not config_path.exists():
        logger.warning("项目缺少 novel.yaml，创建最小配置")
        config_path.write_text(
            'version: "1.0.1"\nmodel: "deepseek/deepseek-v4-flash"\n',
            encoding="utf-8",
        )

    benchmark = RetrievalBenchmark(
        project_root=project_root,
        chapters=args.chapters,
        words_per_chapter=args.words_per_chapter,
        queries=args.queries,
    )
    result = benchmark.run(overwrite=args.overwrite)

    print_results(result)

    if args.output:
        args.output.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n结果已保存: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
