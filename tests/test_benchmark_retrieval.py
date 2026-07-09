"""benchmark_retrieval.py 单元测试。

覆盖：
- 命令行参数解析
- 合成数据生成
- Benchmark 结果 JSON 结构

所有测试使用小规模数据（chapters <= 5），确保快速完成。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.benchmark_retrieval import (
    ARTIFACTS,
    BENCHMARK_QUERIES,
    CHARACTERS,
    LOCATIONS,
    BenchmarkResult,
    RetrievalBenchmark,
    SyntheticNovelGenerator,
    main,
    parse_args,
)


class TestParseArgs:
    """命令行参数解析测试。"""

    def test_default_values(self) -> None:
        """测试默认值。"""
        args = parse_args([])
        assert args.novel == Path("novels/demo_novel")
        assert args.chapters == 20
        assert args.words_per_chapter == 2000
        assert args.queries == 10
        assert args.output is None
        assert args.overwrite is False
        assert args.verbose is False

    def test_custom_values(self) -> None:
        """测试自定义参数。"""
        args = parse_args(
            [
                "--novel",
                "novels/test_novel",
                "--chapters",
                "100",
                "--words-per-chapter",
                "3000",
                "--queries",
                "5",
                "--output",
                "result.json",
                "--overwrite",
                "--verbose",
            ]
        )
        assert args.novel == Path("novels/test_novel")
        assert args.chapters == 100
        assert args.words_per_chapter == 3000
        assert args.queries == 5
        assert args.output == Path("result.json")
        assert args.overwrite is True
        assert args.verbose is True


class TestSyntheticNovelGenerator:
    """合成数据生成测试。"""

    def test_generates_draft_chapters(self, tmp_path: Path) -> None:
        """测试生成 draft 章节文件。"""
        generator = SyntheticNovelGenerator(
            tmp_path,
            chapters=3,
            words_per_chapter=500,
            seed=1,
        )
        stats = generator.generate_all(overwrite=False)

        assert stats["chapters_requested"] == 3
        assert stats["chapters_written"] == 3
        for idx in range(1, 4):
            chapter_file = tmp_path / "draft" / f"ch_{idx:03d}.md"
            assert chapter_file.exists()
            content = chapter_file.read_text(encoding="utf-8")
            assert content.startswith("# ")

    def test_skip_existing_when_no_overwrite(self, tmp_path: Path) -> None:
        """测试未设置 overwrite 时跳过已存在文件。"""
        draft_dir = tmp_path / "draft"
        draft_dir.mkdir(parents=True)
        existing = draft_dir / "ch_001.md"
        existing.write_text("# 已有内容\n\n不要覆盖我。", encoding="utf-8")

        generator = SyntheticNovelGenerator(
            tmp_path,
            chapters=2,
            words_per_chapter=500,
            seed=1,
        )
        stats = generator.generate_all(overwrite=False)

        assert stats["chapters_written"] == 1
        assert existing.read_text(encoding="utf-8") == "# 已有内容\n\n不要覆盖我。"

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        """测试 overwrite 时覆盖已存在文件。"""
        draft_dir = tmp_path / "draft"
        draft_dir.mkdir(parents=True)
        existing = draft_dir / "ch_001.md"
        existing.write_text("# 旧内容", encoding="utf-8")

        generator = SyntheticNovelGenerator(
            tmp_path,
            chapters=1,
            words_per_chapter=500,
            seed=1,
        )
        generator.generate_all(overwrite=True)

        content = existing.read_text(encoding="utf-8")
        assert "旧内容" not in content
        assert "# 第" in content

    def test_generates_canon_and_subconscious(self, tmp_path: Path) -> None:
        """测试生成世界观与潜意识池文件。"""
        generator = SyntheticNovelGenerator(
            tmp_path,
            chapters=2,
            words_per_chapter=500,
            seed=1,
        )
        stats = generator.generate_all()

        canon_file = Path(stats["canon_file"])
        sub_file = Path(stats["subconscious_file"])
        assert canon_file.exists()
        assert sub_file.exists()

        canon_content = canon_file.read_text(encoding="utf-8")
        for loc in LOCATIONS:
            assert loc in canon_content

        sub_content = sub_file.read_text(encoding="utf-8")
        assert "ch_001" in sub_content
        assert "ch_002" in sub_content

    def test_generates_events(self, tmp_path: Path) -> None:
        """测试向 EventStore 写入事件。"""
        generator = SyntheticNovelGenerator(
            tmp_path,
            chapters=2,
            words_per_chapter=500,
            seed=1,
        )
        stats = generator.generate_all()
        assert stats["events_count"] == 2

        from opennovel.storage.sqlite import EventStore

        with EventStore(tmp_path / ".novel.db") as store:
            events = store.get_all_events()
            assert len(events) == 2
            assert events[0].chapter_id == "ch_001"
            assert events[1].chapter_id == "ch_002"
            assert events[0].character_id.startswith("char_")

    def test_chapter_content_includes_entities(self, tmp_path: Path) -> None:
        """测试章节内容包含可检索实体。"""
        generator = SyntheticNovelGenerator(
            tmp_path,
            chapters=1,
            words_per_chapter=800,
            seed=42,
        )
        generator.generate_all()

        content = (tmp_path / "draft" / "ch_001.md").read_text(encoding="utf-8")
        # 至少包含一名角色、一个地点、一个物品
        assert any(name in content for _, name in CHARACTERS)
        assert any(loc in content for loc in LOCATIONS)
        assert any(art in content for art in ARTIFACTS)


class TestRetrievalBenchmark:
    """Benchmark 执行与结果结构测试。"""

    @patch("scripts.benchmark_retrieval.RetrievalBenchmark._build_vector_index")
    @patch("scripts.benchmark_retrieval.RetrievalBenchmark._build_fts5_index")
    @patch("scripts.benchmark_retrieval.RetrievalBenchmark._measure_retrieval")
    def test_run_populates_result(
        self,
        mock_measure: MagicMock,
        mock_fts5: MagicMock,
        mock_vector: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试 run() 返回完整的结果结构。"""
        mock_fts5.return_value = 0.1
        mock_vector.return_value = (0.2, False)

        benchmark = RetrievalBenchmark(
            tmp_path,
            chapters=2,
            words_per_chapter=500,
            queries=3,
        )
        result = benchmark.run(overwrite=True)

        assert isinstance(result, BenchmarkResult)
        assert result.config["chapters"] == 2
        assert result.config["words_per_chapter"] == 500
        assert result.config["queries"] == 3
        assert result.data_generation["chapters_requested"] == 2
        assert result.data_generation["chapters_written"] == 2
        assert result.index_build["fts5_time_ms"] == 100.0
        assert result.index_build["vector_time_ms"] == 200.0
        assert result.index_build["vector_available"] is False
        mock_measure.assert_called_once()

    def test_result_to_dict_is_serializable(self, tmp_path: Path) -> None:
        """测试结果对象可序列化为 JSON。"""
        result = BenchmarkResult()
        result.config = {"chapters": 2}
        result.data_generation = {"total_chars": 1000}
        result.index_build = {"vector_available": True}
        result.retrieval = {"queries_executed": 3}
        result.memory = {"peak_mb": 123.4}
        result.timestamp = "2026-01-01T00:00:00+00:00"

        data = result.to_dict()
        serialized = json.dumps(data)
        parsed = json.loads(serialized)

        assert parsed["config"]["chapters"] == 2
        assert parsed["data_generation"]["total_chars"] == 1000
        assert parsed["index_build"]["vector_available"] is True
        assert parsed["retrieval"]["queries_executed"] == 3
        assert parsed["memory"]["peak_mb"] == 123.4

    def test_measure_reranker_skips_when_unavailable(self, tmp_path: Path) -> None:
        """测试 reranker 不可用时延迟为 0。"""
        benchmark = RetrievalBenchmark(tmp_path, chapters=1, words_per_chapter=200)

        pipeline = MagicMock()
        pipeline.reranker.is_available = False

        latency = benchmark._measure_reranker_latency(pipeline, "查询", "ch_001")
        assert latency == 0.0


class TestBenchmarkQueries:
    """固定查询列表测试。"""

    def test_queries_cover_entities(self) -> None:
        """测试查询列表覆盖主要可检索实体。"""
        query_text = " ".join(BENCHMARK_QUERIES)
        assert any(name in query_text for _, name in CHARACTERS)
        assert any(loc in query_text for loc in LOCATIONS)
        assert any(art in query_text for art in ARTIFACTS)


class TestMain:
    """入口函数测试。"""

    @patch("scripts.benchmark_retrieval.RetrievalBenchmark.run")
    @patch("scripts.benchmark_retrieval.print_results")
    def test_main_creates_config_and_runs(
        self,
        mock_print: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """测试 main 在项目缺少 novel.yaml 时创建配置并运行。"""
        result = BenchmarkResult()
        result.to_dict = MagicMock(return_value={"mock": True})  # type: ignore[method-assign]
        mock_run.return_value = result

        ret = main(["--novel", str(tmp_path), "--chapters", "1", "--queries", "1"])

        assert ret == 0
        assert (tmp_path / "novel.yaml").exists()
        mock_run.assert_called_once_with(overwrite=False)
        mock_print.assert_called_once_with(result)

    def test_main_fails_for_nonexistent_project(self, tmp_path: Path) -> None:
        """测试项目路径不存在时返回非零退出码。"""
        nonexistent = tmp_path / "does_not_exist"
        ret = main(["--novel", str(nonexistent)])
        assert ret == 1


class TestIntegrationSmallScale:
    """小数据量端到端集成测试（快速）。"""

    @patch("scripts.benchmark_retrieval.RetrievalBenchmark._measure_retrieval")
    @patch("scripts.benchmark_retrieval.RetrievalBenchmark._build_vector_index")
    def test_end_to_end_with_temporary_project(
        self,
        mock_vector: MagicMock,
        mock_measure: MagicMock,
        tmp_path: Path,
    ) -> None:
        """在临时项目中完成数据生成与 FTS5 索引构建流程。

        向量索引构建与检索测量因依赖 sentence-transformers 可能较慢，
        此处 mock 掉以保证测试快速完成；FTS5 与数据生成保持真实执行。
        """
        mock_vector.return_value = (0.05, False)

        (tmp_path / "novel.yaml").write_text(
            'version: "1.0.1"\nmodel: "deepseek/deepseek-v4-flash"\n',
            encoding="utf-8",
        )

        benchmark = RetrievalBenchmark(
            tmp_path,
            chapters=2,
            words_per_chapter=400,
            queries=2,
        )
        result = benchmark.run(overwrite=True)

        # 数据生成
        assert result.data_generation["chapters_written"] == 2
        assert result.data_generation["total_chars"] > 0
        assert result.data_generation["events_count"] == 2

        # FTS5 索引真实构建完成
        assert result.index_build["fts5_time_ms"] >= 0
        assert (tmp_path / ".novel.fts5.db").exists()

        # 向量索引与检索被 mock
        assert result.index_build["vector_time_ms"] == 50.0
        assert result.index_build["vector_available"] is False
        mock_measure.assert_called_once()

        # 文件真实存在
        assert (tmp_path / "draft" / "ch_001.md").exists()
        assert (tmp_path / "draft" / "ch_002.md").exists()
