"""YAML Frontmatter / Markdown 文件安全读写层。

这是系统中唯一直接接触文件系统处理 YAML/Markdown 的地方。
所有其他模块必须通过此类进行 Frontmatter 读写操作。

关键约束：
- 写入使用原子操作（tmp + rename），防止断电/崩溃导致文件损坏
- 读取返回字典/文本，不涉及业务模型
- 业务模型转换由调用方负责（如 CharacterFrontmatter(**data)）
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import frontmatter

from opennovel.schemas.character import CharacterFile, CharacterFrontmatter

logger = logging.getLogger(__name__)


class ConflictError(Exception):
    """文件冲突异常：文件已被外部修改，safe_merge 检测到不一致。"""

    pass


class YAMLStorage:
    """YAML/Markdown 文件安全读写层。

    使用方式:
        storage = YAMLStorage()
        metadata, body = storage.read_markdown_file(file_path)
        storage.write_markdown_file(file_path, metadata, body)
    """

    def read_markdown_file(self, file_path: Path) -> tuple[dict[str, Any], str]:
        """解析 Markdown 文件，分离 Frontmatter 和正文。

        Args:
            file_path: Markdown 文件的绝对路径

        Returns:
            元组 (frontmatter_dict, body_text)

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: Frontmatter 格式错误
        """
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        post = frontmatter.load(str(file_path))
        return dict(post.metadata), post.content

    def _atomic_write(self, file_path: Path, content: str) -> None:
        """原子写入：写入临时文件后 rename，防止断电/崩溃导致文件损坏。

        Args:
            file_path: 目标文件路径
            content: 要写入的文本内容
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=file_path.parent,
            prefix=f".tmp_{file_path.stem}_",
            suffix=file_path.suffix,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, file_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def write_markdown_file(
        self,
        file_path: Path,
        metadata: dict[str, Any],
        body: str,
    ) -> None:
        """将 Frontmatter 和正文合并原子写入 Markdown 文件。

        Args:
            file_path: 目标文件路径
            metadata: Frontmatter 字典
            body: 正文内容
        """
        post = frontmatter.Post(body, **metadata)
        self._atomic_write(file_path, frontmatter.dumps(post))

    def update_frontmatter(self, file_path: Path, updates: dict[str, Any]) -> dict[str, Any]:
        """仅更新 Markdown 文件的 Frontmatter 区域，保持正文不变。

        此方法是 Auditor 写入状态的核心入口，确保正文区不被机器篡改。

        Args:
            file_path: 目标文件路径
            updates: 需要更新的 Frontmatter 字段字典

        Returns:
            更新后的完整 Frontmatter 字典
        """
        metadata, body = self.read_markdown_file(file_path)
        metadata.update(updates)
        self.write_markdown_file(file_path, metadata, body)
        return metadata

    def get_frontmatter_value(self, file_path: Path, key: str) -> Any | None:
        """获取 Markdown 文件 Frontmatter 中指定字段的值。

        Args:
            file_path: 文件路径
            key: 字段名

        Returns:
            字段值，若不存在则返回 None
        """
        metadata, _ = self.read_markdown_file(file_path)
        return metadata.get(key)

    def read_body(self, file_path: Path) -> str:
        """仅读取 Markdown 文件的正文内容。

        Args:
            file_path: 文件路径

        Returns:
            正文内容
        """
        _, body = self.read_markdown_file(file_path)
        return body

    def read_character_file(self, file_path: Path) -> CharacterFile:
        """读取角色 Markdown 文件，返回结构化的 CharacterFile 对象。

        Args:
            file_path: 角色 Markdown 文件路径

        Returns:
            包含 Frontmatter 和正文的 CharacterFile 对象
        """
        metadata, body = self.read_markdown_file(file_path)
        return CharacterFile(frontmatter=CharacterFrontmatter(**metadata), body=body)

    def extract_pov_character_id(self, chapter_path: Path) -> str | None:
        """从章节文件中提取 POV 角色 ID。

        Args:
            chapter_path: 章节 Markdown 文件路径

        Returns:
            POV 角色的 Canonical ID，若未指定则返回 None
        """
        return self.get_frontmatter_value(chapter_path, "pov")

    def extract_active_characters(self, chapter_path: Path) -> list[str]:
        """从章节文件中提取活跃角色 ID 列表。

        Args:
            chapter_path: 章节 Markdown 文件路径

        Returns:
            活跃角色的 Canonical ID 列表
        """
        value = self.get_frontmatter_value(chapter_path, "active_characters")
        return value if isinstance(value, list) else []

    def safe_merge(
        self,
        file_path: Path,
        updates: dict[str, Any],
        expected_current: dict[str, Any],
    ) -> dict[str, Any]:
        """带冲突检测的 Frontmatter 更新。

        在写入前比对文件当前 Frontmatter 与 expected_current 是否一致。
        若不一致则抛出 ConflictError，防止覆盖人类在间隙中的手动修改。

        Args:
            file_path: 目标文件路径
            updates: 需要更新的 Frontmatter 字段字典
            expected_current: 期望的当前 Frontmatter 状态（写入前的基准线）

        Returns:
            更新后的完整 Frontmatter 字典

        Raises:
            ConflictError: 文件已被外部修改，与 expected_current 不一致
        """
        actual, body = self.read_markdown_file(file_path)
        for key, expected_value in expected_current.items():
            actual_value = actual.get(key)
            if actual_value != expected_value:
                raise ConflictError(
                    f"字段 '{key}' 已被外部修改: 预期={expected_value!r}, 实际={actual_value!r}"
                )
        actual.update(updates)
        self.write_markdown_file(file_path, actual, body)
        return actual
