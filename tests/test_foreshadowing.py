"""测试伏笔追踪系统。

覆盖范围：
- ForeshadowItem / ForeshadowState 数据模型
- ForeshadowStore 的 Markdown 解析和写入
- 伏笔状态合并逻辑
- Timeline 生成
- Summary 格式化
"""

from pathlib import Path

import pytest

from opennovel.schemas.foreshadowing import (
    ForeshadowItem,
    ForeshadowState,
    ForeshadowStatus,
    ForeshadowType,
)
from opennovel.storage.foreshadowing import ForeshadowStore
from opennovel.storage.summaries import format_summary


class TestForeshadowItem:
    """测试 ForeshadowItem 数据模型。"""

    def test_create(self) -> None:
        item = ForeshadowItem(
            foreshadow_id="F001",
            type=ForeshadowType.PLOT,
            description="那把刀来历不明",
            buried_chapter="ch_001",
            status=ForeshadowStatus.BURIED,
        )
        assert item.foreshadow_id == "F001"
        assert item.type == ForeshadowType.PLOT
        assert item.status == ForeshadowStatus.BURIED
        assert item.related_character_ids == []

    def test_create_with_optional(self) -> None:
        item = ForeshadowItem(
            foreshadow_id="F002",
            type=ForeshadowType.CHARACTER,
            description="角色的秘密",
            buried_chapter="ch_002",
            status=ForeshadowStatus.IN_PROGRESS,
            related_character_ids=["char_001", "char_002"],
            expected_close_chapter="ch_008-ch_010",
            notes="正在推进",
        )
        assert item.related_character_ids == ["char_001", "char_002"]
        assert item.expected_close_chapter == "ch_008-ch_010"
        assert item.notes == "正在推进"

    def test_enum_values(self) -> None:
        for t in ForeshadowType:
            assert t.value in ("plot", "character", "theme", "world")
        for s in ForeshadowStatus:
            assert s.value in ("buried", "in_progress", "closed")


class TestForeshadowStore:
    """测试 ForeshadowStore 的 Markdown 解析和写入。"""

    def test_load_empty(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        state = store.load()
        assert len(state.items) == 0

    def test_save_and_load(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        items = [
            ForeshadowItem(
                foreshadow_id="F001",
                type=ForeshadowType.PLOT,
                description="测试伏笔",
                buried_chapter="ch_001",
                status=ForeshadowStatus.BURIED,
            ),
        ]
        store.save(ForeshadowState(items=items))
        loaded = store.load()
        assert len(loaded.items) == 1
        assert loaded.items[0].foreshadow_id == "F001"
        assert loaded.items[0].description == "测试伏笔"

    def test_merge_new_items(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        current = ForeshadowState(items=[
            ForeshadowItem(
                foreshadow_id="F001",
                type=ForeshadowType.PLOT,
                description="已有伏笔",
                buried_chapter="ch_001",
                status=ForeshadowStatus.BURIED,
            ),
        ])
        new_items = [
            ForeshadowItem(
                foreshadow_id="F002",
                type=ForeshadowType.CHARACTER,
                description="新伏笔",
                buried_chapter="ch_002",
                status=ForeshadowStatus.BURIED,
            ),
        ]
        merged = store.merge_updates(current, new_items)
        assert len(merged.items) == 2
        ids = [i.foreshadow_id for i in merged.items]
        assert "F001" in ids
        assert "F002" in ids

    def test_merge_update_existing(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        current = ForeshadowState(items=[
            ForeshadowItem(
                foreshadow_id="F001",
                type=ForeshadowType.PLOT,
                description="已有伏笔",
                buried_chapter="ch_001",
                status=ForeshadowStatus.BURIED,
            ),
        ])
        updated = [
            ForeshadowItem(
                foreshadow_id="F001",
                type=ForeshadowType.PLOT,
                description="已有伏笔（更新后）",
                buried_chapter="ch_001",
                status=ForeshadowStatus.IN_PROGRESS,
            ),
        ]
        merged = store.merge_updates(current, updated)
        assert len(merged.items) == 1
        assert merged.items[0].status == ForeshadowStatus.IN_PROGRESS
        assert merged.items[0].description == "已有伏笔（更新后）"

    def test_roundtrip_preserves_fields(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        original = ForeshadowState(items=[
            ForeshadowItem(
                foreshadow_id="F001",
                type=ForeshadowType.THEME,
                description="主题伏笔",
                buried_chapter="ch_001",
                status=ForeshadowStatus.CLOSED,
                related_character_ids=["char_001"],
                expected_close_chapter="ch_005",
                notes="已收束",
            ),
        ])
        store.save(original)
        loaded = store.load()
        item = loaded.items[0]
        assert item.foreshadow_id == "F001"
        assert item.type == ForeshadowType.THEME
        assert item.status == ForeshadowStatus.CLOSED
        assert item.related_character_ids == ["char_001"]
        assert item.expected_close_chapter == "ch_005"
        assert item.notes == "已收束"

    def test_markdown_output_format(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        items = [
            ForeshadowItem(
                foreshadow_id="F001",
                type=ForeshadowType.PLOT,
                description="测试伏笔描述",
                buried_chapter="ch_001",
                status=ForeshadowStatus.BURIED,
            ),
        ]
        store.save(ForeshadowState(items=items))

        content = store.file_path.read_text(encoding="utf-8")
        assert "伏笔与暗线追踪" in content
        assert "F001" in content
        assert "测试伏笔描述" in content
        assert "已埋设" in content
        assert "统计" in content

    def test_multiple_items_table(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        items = [
            ForeshadowItem(
                foreshadow_id=f"F{i:03d}",
                type=ForeshadowType.PLOT,
                description=f"伏笔{i}",
                buried_chapter=f"ch_{i:03d}",
                status=ForeshadowStatus.BURIED,
            )
            for i in range(1, 4)
        ]
        store.save(ForeshadowState(items=items))

        content = store.file_path.read_text(encoding="utf-8")
        for i in range(1, 4):
            assert f"F{i:03d}" in content
            assert f"ch_{i:03d}" in content

    def test_parse_table_cjk(self, tmp_path: Path) -> None:
        """测试中文字段名的表格解析。"""
        content = """# 伏笔与暗线追踪

## 伏笔状态表

| ID | 类型 | 描述 | 埋设章节 | 状态 | 关联角色 | 预计回收 | 备注 |
|----|------|------|----------|------|----------|----------|------|
| F001 | 情节 | 中文描述 | ch_001 | 已埋设 | char_001 | ch_008 | 备注 |
| F002 | 角色 | 角色伏笔 | ch_002 | 推进中 | char_001, char_002 | - | - |
| F003 | 主题 | 主题伏笔 | ch_003 | 已收束 | - | ch_010 | 已收束 |
"""
        store = ForeshadowStore(tmp_path)
        store.file_path.parent.mkdir(parents=True, exist_ok=True)
        store.file_path.write_text(content, encoding="utf-8")
        state = store.load()
        assert len(state.items) == 3


class TestSummaries:
    """测试 Summary 格式化。"""

    def test_basic_format(self) -> None:
        result = format_summary(
            chapter_id="ch_001",
            chapter_title="第一章",
            chapter_summary="这是第一章的摘要。",
            total_score=85,
            word_count=5000,
        )
        assert "# ch_001: 第一章" in result
        assert "这是第一章的摘要" in result
        assert "85" in result
        assert "5000" in result

    def test_with_events_and_changes(self) -> None:
        result = format_summary(
            chapter_id="ch_002",
            chapter_title="第二章",
            chapter_summary="摘要内容。",
            key_events=["事件A", "事件B"],
            character_changes=["char_001 受伤", "char_002 获得道具"],
        )
        assert "关键事件" in result
        assert "事件A" in result
        assert "char_001 受伤" in result

    def test_with_mismatches(self) -> None:
        result = format_summary(
            chapter_id="ch_003",
            chapter_title="第三章",
            chapter_summary="摘要。",
            mismatches=["WARNING 角色状态不一致"],
        )
        assert "一致性问题" in result
        assert "WARNING" in result

    def test_empty_summary(self) -> None:
        result = format_summary(
            chapter_id="ch_001",
            chapter_title="标题",
            chapter_summary="",
        )
        assert result.strip() != ""


class TestForeshadowStoreFile:
    """测试 ForeshadowStore 的文件 IO。"""

    def test_atomic_write(self, tmp_path: Path) -> None:
        store = ForeshadowStore(tmp_path)
        items = [ForeshadowItem(
            foreshadow_id="F001",
            type=ForeshadowType.PLOT,
            description="测试",
            buried_chapter="ch_001",
            status=ForeshadowStatus.BURIED,
        )]
        store.save(ForeshadowState(items=items))
        assert store.file_path.exists()
        # 无 tmp 残留
        assert not store.file_path.with_suffix(".md.tmp").exists()

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        """测试深路径自动创建目录。"""
        deep_path = tmp_path / "a" / "b" / "c"
        store = ForeshadowStore(deep_path)
        items = [ForeshadowItem(
            foreshadow_id="F001",
            type=ForeshadowType.PLOT,
            description="测试",
            buried_chapter="ch_001",
            status=ForeshadowStatus.BURIED,
        )]
        store.save(ForeshadowState(items=items))
        assert store.file_path.exists()
        assert store.file_path.read_text(encoding="utf-8") != ""

    def test_corrupted_file(self, tmp_path: Path) -> None:
        """损坏的文件应返回空状态而非崩溃。"""
        store = ForeshadowStore(tmp_path)
        store.file_path.parent.mkdir(parents=True, exist_ok=True)
        store.file_path.write_bytes(b"\xff\xfe\x00\x01")
        state = store.load()
        assert len(state.items) == 0

    def test_empty_table(self, tmp_path: Path) -> None:
        """只有表头没有数据的文件。"""
        content = """# 伏笔与暗线追踪

## 伏笔状态表

| ID | 类型 | 描述 | 埋设章节 | 状态 | 关联角色 | 预计回收 | 备注 |
|----|------|------|----------|------|----------|----------|------|

"""
        store = ForeshadowStore(tmp_path)
        store.file_path.parent.mkdir(parents=True, exist_ok=True)
        store.file_path.write_text(content, encoding="utf-8")
        state = store.load()
        assert len(state.items) == 0
