"""正文与 Shadow 一致性校验器。

检测正文内容与 YAML Frontmatter 状态之间的不一致：
- 伤势一致性：正文提及痊愈但 YAML 仍记为受伤
- 伤势遗漏：正文提及受伤但 YAML 无记录
- 脏标记检测：章节存在 dirty_flag
- 角色引用检测：POV/active_characters 引用不存在的角色

纯规则检测，不依赖 LLM。V1 实现，为后续语义检测留出扩展点。
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from opennovel.storage.yaml_storage import YAMLStorage

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """检测结果严重程度。"""

    WARNING = "WARNING"  # 需要人工关注的不一致
    INFO = "INFO"  # 信息性提示


@dataclass
class Mismatch:
    """一致性检测结果。"""

    severity: Severity
    category: str  # injury / dirty_flag / reference / location / item
    character_id: str  # 涉及的角色 ID，无则为空字符串
    message: str  # 人类可读的描述
    source: str  # 来源文件路径


# ── 关键词表 ──

# 痊愈/恢复类关键词（暗示伤势应被清除）
_HEAL_KEYWORDS = [
    "痊愈",
    "愈合",
    "康复",
    "伤愈",
    "恢复",
    "好了",
    "伤口长好",
    "伤势痊愈",
    "已经好了",
    "不再疼痛",
]

# 受伤类关键词（暗示应有伤势记录）
_INJURY_KEYWORDS = [
    "受伤",
    "骨折",
    "流血",
    "断裂",
    "伤口",
    "伤势",
    "被刺",
    "被砍",
    "被击",
    "断了",
    "碎了",
    "扭伤",
    "剧痛",
    "疼痛",
    "鲜血",
]


class DiffChecker:
    """正文与 Shadow 一致性校验器。

    使用方式:
        checker = DiffChecker(project_root)
        mismatches = checker.check_chapter(chapter_path)
        for m in mismatches:
            print(f"[{m.severity}] {m.message}")
    """

    def __init__(
        self,
        project_root: Path,
        yaml_storage: YAMLStorage | None = None,
    ) -> None:
        """初始化校验器。

        Args:
            project_root: 项目根目录路径
            yaml_storage: YAML 存储实例，默认为 YAMLStorage()
        """
        self.project_root = project_root
        self._yaml_storage = yaml_storage or YAMLStorage()

    def check_chapter(self, chapter_path: Path) -> list[Mismatch]:
        """检查单个章节的一致性。

        Args:
            chapter_path: 章节 Markdown 文件路径

        Returns:
            检测到的不一致列表
        """
        if not chapter_path.exists():
            return [
                Mismatch(
                    severity=Severity.WARNING,
                    category="reference",
                    character_id="",
                    message=f"章节文件不存在: {chapter_path}",
                    source=str(chapter_path),
                )
            ]

        mismatches: list[Mismatch] = []

        # 读取章节 Frontmatter 和正文
        try:
            meta, body = self._yaml_storage.read_markdown_file(chapter_path)
        except (FileNotFoundError, ValueError) as e:
            return [
                Mismatch(
                    severity=Severity.WARNING,
                    category="reference",
                    character_id="",
                    message=f"读取章节失败: {e}",
                    source=str(chapter_path),
                )
            ]

        # 检测脏标记
        mismatches.extend(self._check_dirty_flag(meta, chapter_path))

        # 检测角色引用
        mismatches.extend(self._check_character_references(meta, chapter_path))

        # 检测伤势一致性
        active_chars = meta.get("active_characters", [])
        if isinstance(active_chars, list):
            for char_id in active_chars:
                if not isinstance(char_id, str):
                    continue
                char_mismatches = self._check_injury_consistency(char_id, body, chapter_path)
                mismatches.extend(char_mismatches)

        return mismatches

    def check_all(self) -> list[Mismatch]:
        """扫描所有章节文件，检测一致性。

        Returns:
            所有章节的检测结果汇总
        """
        draft_dir = self.project_root / "draft"
        if not draft_dir.exists():
            return []

        all_mismatches: list[Mismatch] = []
        for chapter_file in sorted(draft_dir.glob("*.md")):
            mismatches = self.check_chapter(chapter_file)
            all_mismatches.extend(mismatches)

        return all_mismatches

    def _check_dirty_flag(self, meta: dict, chapter_path: Path) -> list[Mismatch]:
        """检测章节是否有脏标记。"""
        mismatches: list[Mismatch] = []
        dirty_flag = meta.get("dirty_flag")
        if dirty_flag:
            mismatches.append(
                Mismatch(
                    severity=Severity.WARNING,
                    category="dirty_flag",
                    character_id="",
                    message=(
                        f"章节存在脏标记 (dirty_flag={dirty_flag})，状态不可信，建议重新 commit"
                    ),
                    source=str(chapter_path),
                )
            )
        return mismatches

    def _check_character_references(self, meta: dict, chapter_path: Path) -> list[Mismatch]:
        """检测 POV 和 active_characters 引用的角色是否存在。"""
        mismatches: list[Mismatch] = []
        chars_dir = self.project_root / "characters"

        # 检查 POV 角色
        pov_id = meta.get("pov")
        if pov_id and isinstance(pov_id, str):
            char_path = chars_dir / f"{pov_id}.md"
            if not char_path.exists():
                mismatches.append(
                    Mismatch(
                        severity=Severity.WARNING,
                        category="reference",
                        character_id=pov_id,
                        message=f"POV 角色 {pov_id} 的档案文件不存在: {char_path}",
                        source=str(chapter_path),
                    )
                )

        # 检查活跃角色
        active_chars = meta.get("active_characters", [])
        if isinstance(active_chars, list):
            for char_id in active_chars:
                if not isinstance(char_id, str):
                    continue
                char_path = chars_dir / f"{char_id}.md"
                if not char_path.exists():
                    mismatches.append(
                        Mismatch(
                            severity=Severity.WARNING,
                            category="reference",
                            character_id=char_id,
                            message=f"活跃角色 {char_id} 的档案文件不存在: {char_path}",
                            source=str(chapter_path),
                        )
                    )

        return mismatches

    def _check_injury_consistency(
        self,
        character_id: str,
        chapter_text: str,
        chapter_path: Path,
    ) -> list[Mismatch]:
        """检测角色伤势与正文的一致性。"""
        mismatches: list[Mismatch] = []
        char_path = self.project_root / "characters" / f"{character_id}.md"

        if not char_path.exists():
            return []

        try:
            char_meta, _ = self._yaml_storage.read_markdown_file(char_path)
        except (FileNotFoundError, ValueError):
            return []

        # 获取角色当前伤势列表
        physical = char_meta.get("physical", {})
        injuries = physical.get("injuries", []) if isinstance(physical, dict) else []

        has_injuries = bool(injuries)
        text_lower = chapter_text.lower()

        # 检测：正文提及痊愈但 YAML 仍有伤势
        if has_injuries:
            heal_detected = any(kw in text_lower for kw in _HEAL_KEYWORDS)
            if heal_detected:
                mismatches.append(
                    Mismatch(
                        severity=Severity.WARNING,
                        category="injury",
                        character_id=character_id,
                        message=(
                            f"正文提及 {character_id} 伤势恢复，"
                            f"但 YAML 仍记录 injuries={injuries}，"
                            f"建议执行 loom commit 更新状态"
                        ),
                        source=str(chapter_path),
                    )
                )

        # 检测：正文提及受伤但 YAML 无伤势记录
        if not has_injuries:
            injury_detected = any(kw in text_lower for kw in _INJURY_KEYWORDS)
            if injury_detected:
                mismatches.append(
                    Mismatch(
                        severity=Severity.INFO,
                        category="injury",
                        character_id=character_id,
                        message=(
                            f"正文提及 {character_id} 受伤，"
                            f"但 YAML injuries 为空，"
                            f"建议执行 loom commit 记录状态"
                        ),
                        source=str(chapter_path),
                    )
                )

        return mismatches
