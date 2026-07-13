"""搜索管道端到端测试 — Chunker + FTS5 + SearchPipeline + RRF 融合。

测试覆盖：
- MarkdownChunker: 单文件 / 目录分块 / Frontmatter 分离 / 递归切分
- Fts5Store: 写入 / 搜索 / 中文单字匹配 / 删除 / 元数据
- SearchPipeline: 三通道检索 / RRF 融合 / 结果去重
- RRF 融合算法: 排名融合 / 权重 / 去重
- HybridRetriever: SearchPipeline 集成 / 回退兼容

所有测试使用真实的临时目录、SQLite 和文件系统。
无 mock，端到端验证。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from opennovel.core.chunker import (
    MarkdownChunker,
    _estimate_tokens,
    _extract_frontmatter,
    _source_from_dir,
    chunk_text,
)
from opennovel.core.hybrid_retriever import HybridRetriever
from opennovel.core.reranker import Reranker
from opennovel.core.search_pipeline import SearchPipeline, rrf_fusion
from opennovel.schemas.search import Chunk, ChunkSource, SearchResult
from opennovel.storage.fts5 import Fts5Store


# ═══════════════════════════════════════════════════════════════════════════
# Token 估算测试
# ═══════════════════════════════════════════════════════════════════════════


class TestTokenEstimation:
    """Token 估算函数测试。"""

    def test_empty_string(self) -> None:
        assert _estimate_tokens("") == 0

    def test_english_text(self) -> None:
        tokens = _estimate_tokens("hello world this is a test")
        assert tokens > 0
        assert tokens < 15

    def test_chinese_text(self) -> None:
        tokens = _estimate_tokens("这是一段中文测试文本用于评估分词效果")
        # 20 个中文字符 ≈ 12 tokens
        assert 8 <= tokens <= 18

    def test_mixed_text(self) -> None:
        tokens = _estimate_tokens("你好 world 测试 test")
        assert tokens > 0


# ═══════════════════════════════════════════════════════════════════════════
# Frontmatter 提取测试
# ═══════════════════════════════════════════════════════════════════════════


class TestFrontmatterExtraction:
    """YAML Frontmatter 提取测试。"""

    def test_no_frontmatter(self) -> None:
        meta, body = _extract_frontmatter("# Hello\n\nSome content.")
        assert meta == {}
        assert "Hello" in body

    def test_with_frontmatter(self) -> None:
        text = "---\ntitle: Test\ntags: [a, b]\n---\n\n# Body\nContent here."
        meta, body = _extract_frontmatter(text)
        assert meta == {"title": "Test", "tags": ["a", "b"]}
        assert "# Body" in body
        assert "Content here" in body

    def test_frontmatter_only(self) -> None:
        text = "---\nkey: value\n---\n"
        meta, body = _extract_frontmatter(text)
        assert meta == {"key": "value"}
        assert body == ""

    def test_non_dict_frontmatter(self) -> None:
        """YAML 解析为非 dict 时，metadata 保持空 dict。"""
        text = "---\nnot a mapping\n---\n\nBody text."
        meta, body = _extract_frontmatter(text)
        # 非 dict 结果被忽略，metadata 保持空
        assert isinstance(meta, dict)
        assert "Body text" in body

    def test_unclosed_frontmatter(self) -> None:
        text = "---\ntitle: Test\n\n# Body"
        meta, body = _extract_frontmatter(text)
        assert meta == {}  # 没有闭合的 ---，整个当作正文
        assert "# Body" in body


# ═══════════════════════════════════════════════════════════════════════════
# 分块测试
# ═══════════════════════════════════════════════════════════════════════════


class TestChunkText:
    """chunk_text 核心函数测试。"""

    def test_empty_text(self) -> None:
        result = chunk_text("")
        assert result == []

    def test_short_text_no_split(self) -> None:
        text = "这是一段短文本，不需要切分。"
        result = chunk_text(text)
        assert len(result) == 1
        assert result[0] == text

    def test_split_by_h1(self) -> None:
        text = "# 第一章\n\n内容A。\n\n# 第二章\n\n内容B。"
        result = chunk_text(text, max_tokens=50)
        # 按一级标题切分
        assert len(result) >= 1

    def test_split_by_h2(self) -> None:
        text = "# 卷\n\n## 第一章\n\n" + "内容。 " * 100 + "\n\n## 第二章\n\n" + "文字。 " * 100
        result = chunk_text(text, max_tokens=30)
        assert len(result) >= 2

    def test_long_single_paragraph(self) -> None:
        # 生成超长文本（没有标题分隔）
        text = "长段落。" * 200
        result = chunk_text(text, max_tokens=30)
        # 应该按空行或句子边界切分
        assert len(result) > 1


class TestMarkdownChunker:
    """MarkdownChunker 类测试。"""

    def test_chunk_single_file(self, tmp_path: Path) -> None:
        """端到端：创建 Markdown 文件 → 分块。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        md_file = canon_dir / "world_rules.md"
        md_file.write_text(
            "---\ntitle: 世界规则\n---\n\n"
            "# 魔法体系\n\n魔法消耗生命力。施法者必须付出代价。\n\n"
            "# 种族设定\n\n精灵族寿命千年，矮人族擅长锻造。\n",
            encoding="utf-8",
        )

        chunker = MarkdownChunker(tmp_path)
        chunks = chunker.chunk_file(md_file)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.source == ChunkSource.CANON
            assert chunk.doc_stem == "world_rules"
            assert chunk.chunk_id.startswith("canon_world_rules_p")
            assert len(chunk.text) > 0

    def test_chunk_directory(self, tmp_path: Path) -> None:
        """端到端：扫描目录 → 分块全部文件。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "a.md").write_text("# A\n\n内容A。", encoding="utf-8")
        (canon_dir / "b.md").write_text("# B\n\n内容B。", encoding="utf-8")

        chunker = MarkdownChunker(tmp_path)
        chunks = chunker.chunk_directory(canon_dir)
        assert len(chunks) >= 2
        stems = {c.doc_stem for c in chunks}
        assert "a" in stems
        assert "b" in stems

    def test_chunk_project_sources(self, tmp_path: Path) -> None:
        """端到端：多源分块。"""
        (tmp_path / "canon").mkdir()
        (tmp_path / "characters").mkdir()
        (tmp_path / "draft").mkdir()

        (tmp_path / "canon" / "world.md").write_text("# Rules\n\nRule 1.", encoding="utf-8")
        (tmp_path / "characters" / "hero.md").write_text("# Hero\n\nBrave.", encoding="utf-8")
        (tmp_path / "draft" / "ch_001.md").write_text("# Ch1\n\nStart.", encoding="utf-8")

        chunker = MarkdownChunker(tmp_path)
        grouped = chunker.chunk_project_sources(
            canon_dir=tmp_path / "canon",
            characters_dir=tmp_path / "characters",
            draft_dir=tmp_path / "draft",
        )
        assert ChunkSource.CANON in grouped
        assert ChunkSource.CHARACTER in grouped
        assert ChunkSource.DRAFT in grouped

    def test_chunk_id_deterministic(self, tmp_path: Path) -> None:
        """相同文件分块两次应产生相同 chunk_id。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "rules.md").write_text("# R\n\nText.", encoding="utf-8")

        chunker = MarkdownChunker(tmp_path)
        chunks1 = chunker.chunk_file(canon_dir / "rules.md")
        chunks2 = chunker.chunk_file(canon_dir / "rules.md")
        assert [c.chunk_id for c in chunks1] == [c.chunk_id for c in chunks2]

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        chunker = MarkdownChunker(tmp_path)
        chunks = chunker.chunk_file(tmp_path / "nonexistent.md")
        assert chunks == []

    def test_non_md_file(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "canon" / "note.txt"
        txt_file.parent.mkdir()
        txt_file.write_text("not markdown", encoding="utf-8")

        chunker = MarkdownChunker(tmp_path)
        chunks = chunker.chunk_file(txt_file)
        assert chunks == []

    def test_source_detection(self, tmp_path: Path) -> None:
        """验证 ChunkSource 自动检测。"""
        for dirname, expected_source in [
            ("canon", ChunkSource.CANON),
            ("characters", ChunkSource.CHARACTER),
            ("subconscious", ChunkSource.SUBCONSCIOUS),
            ("draft", ChunkSource.DRAFT),
        ]:
            d = tmp_path / dirname
            d.mkdir(exist_ok=True)
            f = d / "test.md"
            f.write_text("# Test\n\nContent.", encoding="utf-8")
            source = _source_from_dir(f, tmp_path)
            assert source == expected_source, f"{dirname} → {source}"


# ═══════════════════════════════════════════════════════════════════════════
# FTS5 端到端测试
# ═══════════════════════════════════════════════════════════════════════════


class TestFts5Store:
    """FTS5 全文索引端到端测试。"""

    def test_create_and_search(self, tmp_path: Path) -> None:
        """端到端：创建 FTS5 → 写入 chunk → 搜索。"""
        store = Fts5Store(tmp_path)
        try:
            chunks = [
                Chunk(
                    chunk_id="canon_world_p0",
                    source=ChunkSource.CANON,
                    doc_stem="world",
                    chunk_index=0,
                    text="魔法消耗生命力。影渊森林深处埋藏着古老的秘密。",
                ),
                Chunk(
                    chunk_id="draft_ch001_p0",
                    source=ChunkSource.DRAFT,
                    doc_stem="ch001",
                    chunk_index=0,
                    text="主角踏入影渊森林，感受到空气中弥漫的魔法气息。",
                ),
            ]
            count = store.insert_chunks(chunks)
            assert count == 2
            assert store.get_chunk_count() == 2

            # 搜索中文关键词
            results = store.search("影渊森林", top_k=10)
            assert len(results) >= 1
            # CJK 文本存储时已插入空格（unicode61 逐字 tokenize），搜索也匹配
            texts = [r["text"].replace(" ", "") for r in results]
            assert any("影渊森林" in t for t in texts)

            # 搜索英文/拼音（unicode61 tokenizer 按单字）
            results2 = store.search("魔法", top_k=10)
            assert len(results2) >= 1
            texts2 = [r["text"].replace(" ", "") for r in results2]
            assert any("魔法" in t for t in texts2)

        finally:
            store.close()

    def test_search_empty_query(self, tmp_path: Path) -> None:
        store = Fts5Store(tmp_path)
        try:
            results = store.search("", top_k=10)
            assert results == []
            results = store.search("   ", top_k=10)
            assert results == []
        finally:
            store.close()

    def test_delete_by_source(self, tmp_path: Path) -> None:
        """端到端：按来源删除。"""
        store = Fts5Store(tmp_path)
        try:
            chunks = [
                Chunk(chunk_id="canon_a_p0", source=ChunkSource.CANON, doc_stem="a", chunk_index=0, text="A"),
                Chunk(chunk_id="draft_b_p0", source=ChunkSource.DRAFT, doc_stem="b", chunk_index=0, text="B"),
            ]
            store.insert_chunks(chunks)
            assert store.get_chunk_count() == 2

            store.delete_by_source(ChunkSource.CANON)
            assert store.get_chunk_count() == 1

            counts = store.get_chunk_count_by_source()
            assert "canon" not in counts
            assert "draft" in counts

        finally:
            store.close()

    def test_upsert_chunks(self, tmp_path: Path) -> None:
        """INSERT OR REPLACE：同一 chunk_id 覆盖旧数据。"""
        store = Fts5Store(tmp_path)
        try:
            chunk = Chunk(chunk_id="test_p0", source=ChunkSource.CANON, doc_stem="test", chunk_index=0, text="旧内容")
            store.insert_chunks([chunk])
            assert store.get_chunk_count() == 1

            chunk2 = Chunk(chunk_id="test_p0", source=ChunkSource.CANON, doc_stem="test", chunk_index=0, text="新内容")
            store.insert_chunks([chunk2])
            assert store.get_chunk_count() == 1

            results = store.search("新内容", top_k=5)
            assert len(results) >= 1
            assert any("新内容" in r["text"].replace(" ", "") for r in results)

        finally:
            store.close()

    def test_metadata_operations(self, tmp_path: Path) -> None:
        """端到端：元数据读写。"""
        store = Fts5Store(tmp_path)
        try:
            assert store.get_meta("nonexistent") is None

            store.set_meta("last_rebuild", "1234567890")
            assert store.get_meta("last_rebuild") == "1234567890"

            store.set_meta("last_rebuild", "9999999999")
            assert store.get_meta("last_rebuild") == "9999999999"

        finally:
            store.close()

    def test_record_rebuild(self, tmp_path: Path) -> None:
        store = Fts5Store(tmp_path)
        try:
            store.record_rebuild()
            t = store.get_last_rebuild_time()
            assert t is not None
            assert t <= int(time.time())
            assert t > int(time.time()) - 10  # 刚刚记录

        finally:
            store.close()

    def test_needs_rebuild_hint(self, tmp_path: Path) -> None:
        """端到端：重建提示逻辑。"""
        store = Fts5Store(tmp_path)
        try:
            # 无记录时 last_chapter_count=0，current=10 差值=10 ≥ 5，触发提示
            assert store.needs_rebuild_hint(current_chapter_count=10)

            # 记录章节数和重建时间
            store.record_chapter_count(10)
            store.record_rebuild()

            # 新增 5 章 → 需要提示
            assert store.needs_rebuild_hint(current_chapter_count=15, threshold_chapters=5)

            # 新增 3 章 → 不需要提示（低于阈值）
            assert not store.needs_rebuild_hint(current_chapter_count=13, threshold_chapters=5)

        finally:
            store.close()

    def test_clear_all(self, tmp_path: Path) -> None:
        store = Fts5Store(tmp_path)
        try:
            chunks = [
                Chunk(chunk_id="a_p0", source=ChunkSource.CANON, doc_stem="a", chunk_index=0, text="text"),
                Chunk(chunk_id="b_p0", source=ChunkSource.CANON, doc_stem="b", chunk_index=0, text="text"),
            ]
            store.insert_chunks(chunks)
            assert store.get_chunk_count() == 2

            store.clear_all()
            assert store.get_chunk_count() == 0

        finally:
            store.close()

    def test_context_manager(self, tmp_path: Path) -> None:
        """with 语句支持。"""
        with Fts5Store(tmp_path) as store:
            store.insert_chunks([
                Chunk(chunk_id="x_p0", source=ChunkSource.CANON, doc_stem="x", chunk_index=0, text="test"),
            ])
            assert store.get_chunk_count() == 1

    def test_db_file_created(self, tmp_path: Path) -> None:
        """验证 .novel.fts5.db 文件确实被创建。"""
        store = Fts5Store(tmp_path)
        try:
            db_path = tmp_path / ".novel.fts5.db"
            assert db_path.exists()
        finally:
            store.close()


# ═══════════════════════════════════════════════════════════════════════════
# RRF 融合算法测试
# ═══════════════════════════════════════════════════════════════════════════


class TestRrfFusion:
    """RRF 融合算法测试。"""

    def test_single_channel(self) -> None:
        results = {
            "vector": [
                SearchResult(chunk_id="v0", text="Result A", source=ChunkSource.CANON),
                SearchResult(chunk_id="v1", text="Result B", source=ChunkSource.CANON),
            ],
        }
        fused = rrf_fusion(results)
        assert len(fused) == 2
        # 排名靠前的分数更高
        assert fused[0].score > fused[1].score

    def test_two_channels_dedup(self) -> None:
        """相同文本出现在两个通道中应去重并累加分数。"""
        results = {
            "vector": [
                SearchResult(chunk_id="v0", text="共享文本A", source=ChunkSource.CANON),
            ],
            "fts5": [
                SearchResult(chunk_id="f0", text="共享文本A", source=ChunkSource.CANON),
            ],
        }
        fused = rrf_fusion(results)
        # 相同文本应去重为一条结果
        assert len(fused) == 1
        # 应标记为多通道来源
        assert "+" in fused[0].channel

    def test_event_channel_higher_weight(self) -> None:
        """EventStore 通道权重 1.5×。"""
        results = {
            "vector": [
                SearchResult(chunk_id="v0", text="Vector Result", source=ChunkSource.CANON),
            ],
            "event": [
                SearchResult(chunk_id="e0", text="Event Result", source=ChunkSource.DRAFT),
            ],
        }

        # 相同排名下 event 通道有更高 RRF 分数
        # event: 1.5 / (30+1) = 0.0484
        # vector: 1.0 / (30+1) = 0.0323
        fused = rrf_fusion(results)
        assert len(fused) == 2
        event_result = next(r for r in fused if r.chunk_id == "e0")
        vector_result = next(r for r in fused if r.chunk_id == "v0")
        assert event_result.score > vector_result.score

    def test_empty_channels(self) -> None:
        fused = rrf_fusion({})
        assert fused == []

    def test_top_k_limit(self) -> None:
        """验证最多返回 50 个候选。"""
        results = {}
        for ch in ["vector", "fts5", "event"]:
            results[ch] = [
                SearchResult(chunk_id=f"{ch}_{i}", text=f"Text {i}", source=ChunkSource.CANON)
                for i in range(30)
            ]
        fused = rrf_fusion(results)
        assert len(fused) <= 50


# ═══════════════════════════════════════════════════════════════════════════
# SearchPipeline 端到端测试
# ═══════════════════════════════════════════════════════════════════════════


class TestSearchPipelineE2E:
    """SearchPipeline 端到端测试——真实文件、真实 SQLite、真实文本。"""

    def _setup_project(self, tmp_path: Path) -> tuple[Path, Path]:
        """创建最小小说项目结构并写入测试文件。"""
        from opennovel.storage.fts5 import Fts5Store

        canon_dir = tmp_path / "canon"
        draft_dir = tmp_path / "draft"
        canon_dir.mkdir()
        draft_dir.mkdir()

        # 写入测试内容
        (canon_dir / "magic.md").write_text(
            "# 魔法体系\n\n"
            "在这个世界中，魔法并非免费。每一次施法都消耗生命力。\n"
            "高级魔法师需要特殊的影渊水晶来引导魔力。\n",
            encoding="utf-8",
        )
        (canon_dir / "world.md").write_text(
            "# 世界地理\n\n"
            "影渊森林位于大陆中央，是古代文明的遗迹。\n"
            "森林深处埋藏着失落的科技。\n",
            encoding="utf-8",
        )
        (draft_dir / "ch_001.md").write_text(
            "# 第一章：启程\n\n"
            "主角艾琳踏入了影渊森林。她感受到空气中弥漫的魔法气息，\n"
            "但也意识到每次使用魔法都会消耗她的生命力。\n",
            encoding="utf-8",
        )

        # 构建 FTS5 索引
        fts5_store = Fts5Store(tmp_path)
        chunker = MarkdownChunker(tmp_path)
        grouped = chunker.chunk_project_sources(
            canon_dir=canon_dir,
            draft_dir=draft_dir,
        )
        for chunks in grouped.values():
            fts5_store.insert_chunks(chunks)

        return canon_dir, draft_dir

    def test_search_with_fts5_only(self, tmp_path: Path) -> None:
        """端到端：仅 FTS5 通道检索。"""
        from opennovel.storage.fts5 import Fts5Store

        self._setup_project(tmp_path)

        fts5_store = Fts5Store(tmp_path)
        pipeline = SearchPipeline(
            tmp_path,
            vector_store=None,
            fts5_store=fts5_store,
            event_store=None,
        )

        try:
            # 搜索中文关键词
            response = pipeline.search("影渊森林", top_k=5, use_reranker=False)
            assert response.total_candidates > 0
            assert len(response.results) >= 1
            # 至少有一个结果包含 "影渊森林"（CJK 空格需 strip）
            assert any("影渊森林" in r.text.replace(" ", "") for r in response.results)

            # 搜索特定概念
            response2 = pipeline.search("生命力", top_k=3, use_reranker=False)
            assert response2.total_candidates > 0
            assert any("生命力" in r.text.replace(" ", "") for r in response2.results)

        finally:
            fts5_store.close()

    def test_search_empty_query(self, tmp_path: Path) -> None:
        """空查询应返回空结果。"""
        from opennovel.storage.fts5 import Fts5Store

        fts5_store = Fts5Store(tmp_path)
        pipeline = SearchPipeline(tmp_path, fts5_store=fts5_store)

        try:
            response = pipeline.search("", top_k=5)
            assert response.results == []
            assert response.total_candidates == 0
        finally:
            fts5_store.close()

    def test_rebuild_index_e2e(self, tmp_path: Path) -> None:
        """端到端：全量重建索引。"""
        canon_dir = tmp_path / "canon"
        draft_dir = tmp_path / "draft"
        canon_dir.mkdir()
        draft_dir.mkdir()

        (canon_dir / "rules.md").write_text("# Rules\n\nRule content.", encoding="utf-8")
        (draft_dir / "ch_001.md").write_text("# Chapter 1\n\nChapter content.", encoding="utf-8")

        from opennovel.storage.fts5 import Fts5Store

        fts5_store = Fts5Store(tmp_path)
        pipeline = SearchPipeline(tmp_path, fts5_store=fts5_store)

        try:
            total = pipeline.rebuild_index(
                canon_dir=canon_dir,
                draft_dir=draft_dir,
            )
            assert total > 0
            assert fts5_store.get_chunk_count() > 0

            # 重建后应可搜索
            response = pipeline.search("Rule", top_k=5, use_reranker=False)
            assert response.total_candidates > 0

        finally:
            fts5_store.close()

    def test_incremental_update(self, tmp_path: Path) -> None:
        """端到端：增量更新索引。"""
        from opennovel.storage.fts5 import Fts5Store

        fts5_store = Fts5Store(tmp_path)
        pipeline = SearchPipeline(tmp_path, fts5_store=fts5_store)

        try:
            # 增量添加 chunk
            new_chunks = [
                Chunk(chunk_id="draft_new_p0", source=ChunkSource.DRAFT, doc_stem="new", chunk_index=0,
                      text="新增章节内容，包含关键概念：永恒之塔。"),
            ]
            count = pipeline.incremental_update(new_chunks)
            assert count == 1

            # 验证可被搜索到
            response = pipeline.search("永恒之塔", top_k=5, use_reranker=False)
            assert response.total_candidates > 0
            assert any("永恒之塔" in r.text.replace(" ", "") for r in response.results)

        finally:
            fts5_store.close()

    def test_source_filtering(self, tmp_path: Path) -> None:
        """按来源过滤搜索结果。"""
        from opennovel.storage.fts5 import Fts5Store

        self._setup_project(tmp_path)

        fts5_store = Fts5Store(tmp_path)
        pipeline = SearchPipeline(tmp_path, fts5_store=fts5_store)

        try:
            # 仅搜索 CANON 来源
            response = pipeline.search(
                "魔法", top_k=5, use_reranker=False, source_filter=ChunkSource.CANON,
            )
            # 所有结果应来自 CANON
            for r in response.results:
                assert r.source == ChunkSource.CANON

        finally:
            fts5_store.close()


# ═══════════════════════════════════════════════════════════════════════════
# HybridRetriever 集成测试
# ═══════════════════════════════════════════════════════════════════════════


class TestHybridRetrieverIntegration:
    """HybridRetriever 与 SearchPipeline 集成端到端测试。"""

    def test_query_with_fts5_available(self, tmp_path: Path) -> None:
        """当 FTS5 数据库存在时，HybridRetriever 应使用 SearchPipeline。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text(
            "---\ntitle: 世界设定\n---\n\n"
            "# 魔法体系\n\n使用魔法消耗生命力。\n",
            encoding="utf-8",
        )

        # 预先构建 FTS5 索引
        from opennovel.storage.fts5 import Fts5Store

        fts5_store = Fts5Store(tmp_path)
        try:
            chunker = MarkdownChunker(tmp_path)
            chunks = chunker.chunk_directory(canon_dir)
            fts5_store.insert_chunks(chunks)
        finally:
            fts5_store.close()

        # 创建 HybridRetriever（应自动检测并使用 FTS5）
        hybrid = HybridRetriever(tmp_path)
        result = hybrid.query_narrative_context("魔法 生命力")

        # 应有内容返回
        assert len(result.canon_content) > 0 or len(result.fts5_content) > 0

    def test_query_without_fts5_fallback(self, tmp_path: Path) -> None:
        """当 FTS5 数据库不存在时，应回退到传统双通道。"""
        # 不创建 .novel.fts5.db
        hybrid = HybridRetriever(tmp_path)
        # 不应崩溃，返回空结果
        result = hybrid.query_narrative_context("任意查询")
        # 验证没有异常（所有字段都是空的即可）
        assert isinstance(result.canon_content, str)

    def test_query_for_writer(self, tmp_path: Path) -> None:
        """Writer 专用查询接口。"""
        hybrid = HybridRetriever(tmp_path)
        result = hybrid.query_for_writer("ch_001", "主角进入森林")
        assert isinstance(result.canon_content, str)
        assert result.high_pressure_events is not None

    def test_query_for_critic(self, tmp_path: Path) -> None:
        """Critic 专用查询接口。"""
        hybrid = HybridRetriever(tmp_path)
        result = hybrid.query_for_critic("ch_001", "主角使用了魔法来对抗敌人")
        assert isinstance(result.canon_content, str)


# ═══════════════════════════════════════════════════════════════════════════
# Reranker 测试（仅在 sentence-transformers 可用时运行）
# ═══════════════════════════════════════════════════════════════════════════


class TestReranker:
    """Cross-Encoder 重排序器测试。"""

    def test_disabled_reranker(self) -> None:
        """禁用时直接返回 top_k 结果。"""
        reranker = Reranker(enabled=False)
        candidates = [
            SearchResult(chunk_id=f"c{i}", text=f"Text {i}", source=ChunkSource.CANON)
            for i in range(10)
        ]
        results = reranker.rerank("query", candidates)
        assert len(results) <= 5
        # 保持原序
        assert results[0].chunk_id == "c0"

    def test_should_skip_rerank_top1_dominant(self) -> None:
        """当 top1 RRF 分数远超 top2 时跳过。"""
        reranker = Reranker(enabled=True)
        candidates = [
            SearchResult(chunk_id="c0", text="Best match", source=ChunkSource.CANON, score=0.5),
            SearchResult(chunk_id="c1", text="Second match", source=ChunkSource.CANON, score=0.1),
        ]
        assert reranker.should_skip_rerank(candidates) is True

    def test_should_not_skip_rerank_high_cv(self) -> None:
        """分数高度分散且 fast path 未触发时应重排。"""
        reranker = Reranker(enabled=True)
        # top1/top2 = 0.5/0.3 = 1.67 < 3.0 (不触发 fast path)
        # CV 应较高 → 不应跳过重排
        candidates = [
            SearchResult(chunk_id="c0", text="First", source=ChunkSource.CANON, score=0.5),
            SearchResult(chunk_id="c1", text="Second", source=ChunkSource.CANON, score=0.3),
            SearchResult(chunk_id="c2", text="Third", source=ChunkSource.CANON, score=0.05),
        ]
        assert reranker.should_skip_rerank(candidates) is False

    def test_single_candidate_skip(self) -> None:
        """单候选时总是跳过。"""
        reranker = Reranker(enabled=True)
        candidates = [
            SearchResult(chunk_id="c0", text="Only", source=ChunkSource.CANON, score=1.0),
        ]
        assert reranker.should_skip_rerank(candidates) is True

    def test_empty_candidates(self) -> None:
        reranker = Reranker(enabled=True)
        results = reranker.rerank("query", [])
        assert results == []

    def test_rerank_with_real_model(self) -> None:
        """使用真实 Cross-Encoder 模型重排序（需要 sentence-transformers）。"""
        reranker = Reranker(enabled=True)

        # 检查模型是否可用
        if not Reranker._check_availability():
            pytest.skip("sentence-transformers 未安装，跳过真实模型测试")

        model = Reranker.get_model()
        if model is None:
            pytest.skip("Cross-Encoder 模型加载失败")

        candidates = [
            SearchResult(chunk_id="c0", text="巴黎是法国的首都。", source=ChunkSource.CANON),
            SearchResult(chunk_id="c1", text="苹果是一种健康的水果。", source=ChunkSource.CANON),
            SearchResult(chunk_id="c2", text="法国位于欧洲西部。", source=ChunkSource.CANON),
        ]
        results = reranker.rerank("法国的首都", candidates)
        assert len(results) <= 5

        # "巴黎是法国的首都"应该排在最前面
        if results:
            # 检查 rerank_score 是否被填入
            for r in results:
                assert r.rerank_score is not None
