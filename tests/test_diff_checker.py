"""diff_checker 模块测试 - 正文与 Shadow 一致性校验。"""

from pathlib import Path
from unittest.mock import patch

import pytest

from opennovel.core.diff_checker import DiffChecker, Mismatch, Severity
from opennovel.storage.yaml_storage import YAMLStorage


@pytest.fixture
def storage() -> YAMLStorage:
    """创建 YAMLStorage 实例。"""
    return YAMLStorage()


@pytest.fixture
def project_root(tmp_path: Path, storage: YAMLStorage) -> Path:
    """创建测试用项目目录，包含角色和章节文件。"""
    root = tmp_path / "test_project"
    root.mkdir()

    # 创建目录
    (root / "characters").mkdir()
    (root / "draft").mkdir()
    (root / "canon").mkdir()
    (root / "subconscious").mkdir()

    # 创建角色文件：有伤势
    char_meta = {
        "id": "char_001",
        "name": "林夜",
        "physical": {"injuries": ["left_arm_fracture"], "buffs": [], "debuffs": []},
        "location": "loc_tower",
        "inventory": ["item_sword"],
    }
    storage.write_markdown_file(
        root / "characters" / "char_001.md",
        char_meta,
        "# 林夜\n\n主角背景故事。",
    )

    # 创建角色文件：无伤势
    char2_meta = {
        "id": "char_002",
        "name": "苏雨",
        "physical": {"injuries": [], "buffs": [], "debuffs": []},
        "location": "loc_village",
        "inventory": [],
    }
    storage.write_markdown_file(
        root / "characters" / "char_002.md",
        char2_meta,
        "# 苏雨\n\n配角背景故事。",
    )

    return root


class TestSeverityAndMismatch:
    """Severity 枚举和 Mismatch 数据类测试。"""

    def test_severity_values(self) -> None:
        """测试严重程度枚举值。"""
        assert Severity.WARNING.value == "WARNING"
        assert Severity.INFO.value == "INFO"

    def test_mismatch_creation(self) -> None:
        """测试 Mismatch 对象创建。"""
        m = Mismatch(
            severity=Severity.WARNING,
            category="injury",
            character_id="char_001",
            message="测试警告",
            source="test.md",
        )
        assert m.severity == Severity.WARNING
        assert m.category == "injury"
        assert m.character_id == "char_001"


class TestInjuryConsistency:
    """伤势一致性检测测试。"""

    def test_heal_keyword_but_still_injured(self, project_root: Path, storage: YAMLStorage) -> None:
        """正文提及痊愈但 YAML 仍记录伤势 → 应检测到不一致。"""
        chapter_path = project_root / "draft" / "ch_001.md"
        chapter_meta = {
            "id": "ch_001",
            "pov": "char_001",
            "active_characters": ["char_001"],
        }
        storage.write_markdown_file(
            chapter_path,
            chapter_meta,
            "# 第一章\n\n林夜的左臂已经完全痊愈了。",
        )

        checker = DiffChecker(project_root)
        mismatches = checker.check_chapter(chapter_path)

        # 应检测到：痊愈但 injuries 仍有记录
        injury_mismatches = [m for m in mismatches if m.category == "injury"]
        assert len(injury_mismatches) >= 1
        assert any("痊愈" in m.message or "injuries" in m.message for m in injury_mismatches)

    def test_injured_text_no_injuries_recorded(
        self, project_root: Path, storage: YAMLStorage
    ) -> None:
        """正文提及受伤但 YAML 无伤势记录 → 应检测到遗漏。"""
        chapter_path = project_root / "draft" / "ch_002.md"
        chapter_meta = {
            "id": "ch_002",
            "pov": "char_002",
            "active_characters": ["char_002"],
        }
        storage.write_markdown_file(
            chapter_path,
            chapter_meta,
            "# 第二章\n\n苏雨的手臂骨折了，鲜血直流。",
        )

        checker = DiffChecker(project_root)
        mismatches = checker.check_chapter(chapter_path)

        injury_mismatches = [m for m in mismatches if m.category == "injury"]
        assert len(injury_mismatches) >= 1

    def test_consistent_injuries_no_warning(self, project_root: Path, storage: YAMLStorage) -> None:
        """正文提及受伤且 YAML 已记录伤势 → 不应警告。"""
        chapter_path = project_root / "draft" / "ch_003.md"
        chapter_meta = {
            "id": "ch_003",
            "pov": "char_001",
            "active_characters": ["char_001"],
        }
        storage.write_markdown_file(
            chapter_path,
            chapter_meta,
            "# 第三章\n\n林夜忍着左臂的剧痛继续前行。",
        )

        checker = DiffChecker(project_root)
        mismatches = checker.check_chapter(chapter_path)

        injury_mismatches = [m for m in mismatches if m.category == "injury"]
        # 正文提及疼痛但 YAML 已有记录，不应警告
        assert len(injury_mismatches) == 0


class TestDirtyFlagDetection:
    """脏标记检测测试。"""

    def test_dirty_flag_detected(self, project_root: Path, storage: YAMLStorage) -> None:
        """章节有 dirty_flag → 应检测到警告。"""
        chapter_path = project_root / "draft" / "ch_dirty.md"
        chapter_meta = {
            "id": "ch_dirty",
            "pov": "char_001",
            "active_characters": ["char_001"],
            "dirty_flag": "extraction_failed",
        }
        storage.write_markdown_file(chapter_path, chapter_meta, "# 脏章节\n\n内容")

        checker = DiffChecker(project_root)
        mismatches = checker.check_chapter(chapter_path)

        dirty_mismatches = [m for m in mismatches if m.category == "dirty_flag"]
        assert len(dirty_mismatches) == 1
        assert dirty_mismatches[0].severity == Severity.WARNING


class TestCharacterReferenceCheck:
    """角色引用检测测试。"""

    def test_pov_character_not_found(self, project_root: Path, storage: YAMLStorage) -> None:
        """章节 POV 引用不存在的角色 → 应检测到。"""
        chapter_path = project_root / "draft" / "ch_ghost.md"
        chapter_meta = {
            "id": "ch_ghost",
            "pov": "char_999",
            "active_characters": ["char_999"],
        }
        storage.write_markdown_file(chapter_path, chapter_meta, "# 幽灵章节\n\n内容")

        checker = DiffChecker(project_root)
        mismatches = checker.check_chapter(chapter_path)

        ref_mismatches = [m for m in mismatches if m.category == "reference"]
        assert len(ref_mismatches) >= 1


class TestCheckAll:
    """全量扫描测试。"""

    def test_check_all_chapters(self, project_root: Path, storage: YAMLStorage) -> None:
        """check_all 应扫描所有章节文件。"""
        # 创建两个章节
        for i in range(1, 3):
            chapter_path = project_root / "draft" / f"ch_{i:03d}.md"
            storage.write_markdown_file(
                chapter_path,
                {"id": f"ch_{i:03d}", "pov": "char_001", "active_characters": ["char_001"]},
                f"# 第{i}章\n\n内容",
            )

        checker = DiffChecker(project_root)
        all_mismatches = checker.check_all()
        assert isinstance(all_mismatches, list)

    def test_check_all_empty_project(self, tmp_path: Path) -> None:
        """空项目不应报错。"""
        root = tmp_path / "empty"
        root.mkdir()
        (root / "draft").mkdir()

        checker = DiffChecker(root)
        mismatches = checker.check_all()
        assert mismatches == []


class TestErrorHandling:
    """错误处理路径测试。"""

    def test_check_chapter_nonexistent_file(self, tmp_path: Path) -> None:
        """章节文件不存在时应返回 WARNING 消息（覆盖 line 110）。"""
        root = tmp_path / "proj"
        root.mkdir()
        checker = DiffChecker(root)
        nonexistent = root / "draft" / "missing.md"

        mismatches = checker.check_chapter(nonexistent)

        assert len(mismatches) == 1
        assert mismatches[0].severity == Severity.WARNING
        assert mismatches[0].category == "reference"
        assert "不存在" in mismatches[0].message

    def test_check_chapter_read_failure(self, project_root: Path, storage: YAMLStorage) -> None:
        """读取章节文件失败时应返回 WARNING 消息（覆盖 lines 125-126）。"""
        chapter_path = project_root / "draft" / "ch_read_err.md"
        # 写入一个合法文件
        storage.write_markdown_file(chapter_path, {"id": "ch_read_err"}, "# 内容")

        checker = DiffChecker(project_root, yaml_storage=storage)

        # 模拟 read_markdown_file 抛出 ValueError
        with patch.object(storage, "read_markdown_file", side_effect=ValueError("YAML 格式损坏")):
            mismatches = checker.check_chapter(chapter_path)

        assert len(mismatches) == 1
        assert mismatches[0].severity == Severity.WARNING
        assert "读取章节失败" in mismatches[0].message

    def test_check_all_no_draft_dir(self, tmp_path: Path) -> None:
        """draft 目录不存在时 check_all 应返回空列表（覆盖 line 161）。"""
        root = tmp_path / "proj_no_draft"
        root.mkdir()
        # 不创建 draft 目录

        checker = DiffChecker(root)
        mismatches = checker.check_all()

        assert mismatches == []

    def test_active_characters_with_non_string_items_in_reference_check(
        self, project_root: Path, storage: YAMLStorage
    ) -> None:
        """active_characters 含非字符串元素时应安全跳过（覆盖 line 213）。"""
        chapter_path = project_root / "draft" / "ch_mixed.md"
        chapter_meta = {
            "id": "ch_mixed",
            "pov": "char_001",
            "active_characters": ["char_001", 123, None, "char_002"],
        }
        storage.write_markdown_file(chapter_path, chapter_meta, "# 混合引用")

        checker = DiffChecker(project_root, yaml_storage=storage)
        mismatches = checker.check_chapter(chapter_path)

        # 不应崩溃，非字符串项被跳过
        assert isinstance(mismatches, list)

    def test_active_characters_with_non_string_items_in_injury_check(
        self, project_root: Path, storage: YAMLStorage
    ) -> None:
        """伤势检测中 active_characters 含非字符串元素时应安全跳过（覆盖 line 147）。"""
        chapter_path = project_root / "draft" / "ch_injury_mixed.md"
        chapter_meta = {
            "id": "ch_injury_mixed",
            "pov": "char_001",
            "active_characters": ["char_001", 42, True],
        }
        storage.write_markdown_file(chapter_path, chapter_meta, "# 第一章\n\n林夜的左臂受伤了。")

        checker = DiffChecker(project_root, yaml_storage=storage)
        mismatches = checker.check_chapter(chapter_path)

        # 不应崩溃，42 和 True 被跳过
        assert isinstance(mismatches, list)
        # char_001 的伤势检测应正常执行
        injury_mismatches = [m for m in mismatches if m.category == "injury"]
        assert all(m.character_id != "42" for m in injury_mismatches)

    def test_injury_consistency_char_read_failure(
        self, project_root: Path, storage: YAMLStorage
    ) -> None:
        """角色文件读取失败时伤势检测应安全返回空列表（覆盖 lines 243-244）。"""
        chapter_path = project_root / "draft" / "ch_injury_err.md"
        chapter_meta = {
            "id": "ch_injury_err",
            "pov": "char_001",
            "active_characters": ["char_001"],
        }
        storage.write_markdown_file(chapter_path, chapter_meta, "# 第一章\n\n林夜痊愈了。")

        checker = DiffChecker(project_root, yaml_storage=storage)

        # 模拟 read_markdown_file 抛出 FileNotFoundError
        original_read = storage.read_markdown_file

        def mock_read(file_path: Path):
            if "char_001" in str(file_path):
                raise FileNotFoundError("角色文件损坏")
            return original_read(file_path)

        with patch.object(storage, "read_markdown_file", side_effect=mock_read):
            mismatches = checker.check_chapter(chapter_path)

        # 不应崩溃，伤势检测返回空
        assert isinstance(mismatches, list)
        injury_mismatches = [m for m in mismatches if m.category == "injury"]
        assert len(injury_mismatches) == 0
