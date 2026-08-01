"""YAMLStorage 模块测试 - Markdown/Frontmatter 读写与原子写入。"""

from pathlib import Path
from unittest.mock import patch

import pytest

from opennovel.storage.yaml_storage import ConflictError, YAMLStorage


@pytest.fixture
def storage() -> YAMLStorage:
    """创建 YAMLStorage 实例。"""
    return YAMLStorage()


@pytest.fixture
def sample_md_file(tmp_path: Path, storage: YAMLStorage) -> Path:
    """创建测试用 Markdown 文件。"""
    file_path = tmp_path / "test_char.md"
    metadata = {
        "id": "char_001",
        "name": "Alice",
        "aliases": ["Alys"],
        "location": "loc_tower",
    }
    body = "# Alice 的背景故事\n\n她来自北方..."
    storage.write_markdown_file(file_path, metadata, body)
    return file_path


@pytest.fixture
def sample_chapter_file(tmp_path: Path, storage: YAMLStorage) -> Path:
    """创建测试用章节文件。"""
    file_path = tmp_path / "ch_001.md"
    metadata = {
        "id": "ch_001",
        "title": "第一章",
        "pov": "char_001",
        "active_characters": ["char_001", "char_002"],
    }
    body = "# 第一章\n\n故事开始了..."
    storage.write_markdown_file(file_path, metadata, body)
    return file_path


class TestReadWrite:
    """读写操作测试。"""

    def test_read_write_roundtrip(self, storage: YAMLStorage, tmp_path: Path) -> None:
        """测试写入后读取的完整性。"""
        file_path = tmp_path / "output.md"
        metadata = {"id": "char_002", "name": "Bob"}
        body = "Bob 的故事"

        storage.write_markdown_file(file_path, metadata, body)
        read_meta, read_body = storage.read_markdown_file(file_path)

        assert read_meta["id"] == "char_002"
        assert read_meta["name"] == "Bob"
        assert "Bob 的故事" in read_body

    def test_read_nonexistent(self, storage: YAMLStorage, tmp_path: Path) -> None:
        """测试读取不存在的文件。"""
        with pytest.raises(FileNotFoundError):
            storage.read_markdown_file(tmp_path / "nonexistent.md")

    def test_atomic_write_creates_parent_dirs(self, storage: YAMLStorage, tmp_path: Path) -> None:
        """测试原子写入自动创建父目录。"""
        file_path = tmp_path / "nested" / "dir" / "test.md"
        storage.write_markdown_file(file_path, {"key": "value"}, "content")
        assert file_path.exists()

    def test_atomic_write_content_integrity(self, storage: YAMLStorage, tmp_path: Path) -> None:
        """测试原子写入后文件内容完整。"""
        file_path = tmp_path / "atomic_test.md"
        metadata = {"id": "test", "values": [1, 2, 3]}
        body = "正文内容"
        storage.write_markdown_file(file_path, metadata, body)

        raw = file_path.read_text(encoding="utf-8")
        assert "id: test" in raw
        assert "values:" in raw
        assert "正文内容" in raw


class TestUpdateFrontmatter:
    """Frontmatter 更新测试。"""

    def test_update_preserves_body(self, storage: YAMLStorage, sample_md_file: Path) -> None:
        """测试更新 Frontmatter 时保持正文不变。"""
        updated = storage.update_frontmatter(sample_md_file, {"name": "Alice Updated"})
        assert updated["name"] == "Alice Updated"
        assert updated["id"] == "char_001"

        _, body = storage.read_markdown_file(sample_md_file)
        assert "Alice 的背景故事" in body

    def test_update_adds_new_field(self, storage: YAMLStorage, sample_md_file: Path) -> None:
        """测试在 Frontmatter 中添加新字段。"""
        updated = storage.update_frontmatter(sample_md_file, {"age": 25})
        assert updated["age"] == 25


class TestSafeMerge:
    """safe_merge 冲突检测测试。"""

    def test_no_conflict(self, storage: YAMLStorage, sample_md_file: Path) -> None:
        """测试无冲突时正常更新。"""
        current = storage.read_markdown_file(sample_md_file)[0]
        updated = storage.safe_merge(
            sample_md_file,
            updates={"name": "Alice V2"},
            expected_current={"name": current["name"]},
        )
        assert updated["name"] == "Alice V2"

    def test_detects_conflict(self, storage: YAMLStorage, sample_md_file: Path) -> None:
        """测试检测到冲突时抛出 ConflictError。"""
        with pytest.raises(ConflictError):
            storage.safe_merge(
                sample_md_file,
                updates={"name": "Alice V2"},
                expected_current={"name": "WrongName"},
            )


class TestExtractFunctions:
    """提取辅助函数测试。"""

    def test_extract_pov(self, storage: YAMLStorage, sample_chapter_file: Path) -> None:
        """测试提取 POV 角色 ID。"""
        pov = storage.extract_pov_character_id(sample_chapter_file)
        assert pov == "char_001"

    def test_extract_active_characters(
        self, storage: YAMLStorage, sample_chapter_file: Path
    ) -> None:
        """测试提取活跃角色列表。"""
        chars = storage.extract_active_characters(sample_chapter_file)
        assert chars == ["char_001", "char_002"]

    def test_extract_pov_missing(self, storage: YAMLStorage, tmp_path: Path) -> None:
        """测试无 POV 字段时返回 None。"""
        file_path = tmp_path / "no_pov.md"
        storage.write_markdown_file(file_path, {"id": "ch_002"}, "content")
        assert storage.extract_pov_character_id(file_path) is None

    def test_read_body(self, storage: YAMLStorage, sample_md_file: Path) -> None:
        """测试仅读取正文。"""
        body = storage.read_body(sample_md_file)
        assert "Alice 的背景故事" in body
        assert "---" not in body  # Frontmatter 分隔符不应在正文中

    def test_read_character_file(self, storage: YAMLStorage, sample_md_file: Path) -> None:
        """测试读取角色文件为结构化对象。"""
        char_file = storage.read_character_file(sample_md_file)
        assert char_file.frontmatter.id == "char_001"
        assert char_file.frontmatter.name == "Alice"
        assert "Alice 的背景故事" in char_file.body

    def test_get_frontmatter_value(self, storage: YAMLStorage, sample_md_file: Path) -> None:
        """测试获取单个 Frontmatter 字段。"""
        value = storage.get_frontmatter_value(sample_md_file, "name")
        assert value == "Alice"
        assert storage.get_frontmatter_value(sample_md_file, "nonexistent") is None


class TestAtomicWriteCleanup:
    """原子写入异常清理路径测试。"""

    def test_atomic_write_cleans_up_temp_on_failure(
        self, storage: YAMLStorage, tmp_path: Path
    ) -> None:
        """写入过程中发生异常时应清理临时文件并重新抛出（覆盖 lines 75-78）。"""
        file_path = tmp_path / "test_cleanup.md"
        file_path.write_text("original content", encoding="utf-8")

        # 记录写入前目录中的文件
        files_before = set(tmp_path.iterdir())

        # 模拟 os.replace 抛出异常，触发 except 分支
        with (
            patch("opennovel.storage.yaml_storage.os.replace", side_effect=OSError("模拟磁盘错误")),
            pytest.raises(OSError, match="模拟磁盘错误"),
        ):
            storage.write_markdown_file(file_path, {"id": "test"}, "新内容")

        # 验证临时文件已被清理（不应多出残留临时文件）
        files_after = set(tmp_path.iterdir())
        new_files = files_after - files_before
        # 不应存在 .tmp_ 前缀的临时文件
        tmp_files = [f for f in new_files if f.name.startswith(".tmp_")]
        assert len(tmp_files) == 0, f"临时文件未被清理: {tmp_files}"

        # 原文件内容应保持不变（未被覆盖）
        assert file_path.read_text(encoding="utf-8") == "original content"

    def test_atomic_write_no_temp_file_to_cleanup(
        self, storage: YAMLStorage, tmp_path: Path
    ) -> None:
        """临时文件已不存在时清理分支应安全跳过（覆盖 line 76 的 False 分支）。"""
        file_path = tmp_path / "test_no_tmp.md"

        # 模拟 os.replace 失败，同时 os.path.exists 返回 False（临时文件已消失）
        with (
            patch("opennovel.storage.yaml_storage.os.replace", side_effect=OSError("错误")),
            patch("opennovel.storage.yaml_storage.os.path.exists", return_value=False),
            pytest.raises(OSError),
        ):
            storage.write_markdown_file(file_path, {"id": "test"}, "内容")

        # 不应崩溃，安全跳过清理
