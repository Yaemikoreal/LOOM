"""Markdown 递归分块器。

按 Markdown 标题层级递归分块：
1. `#` 一级标题 → 顶级边界
2. 超 max_chunk_tokens → `##` 二级标题切割
3. 仍超限 → 空行段落切割
4. 仍超限 → 句末边界（。！？. ! ?）切割

分离 YAML Frontmatter 存入 metadata，生成确定性 chunk_id。

详见 docs/adr/0007-hybrid-search-and-reranking-architecture.md。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from opennovel.schemas.search import Chunk, ChunkSource

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────
DEFAULT_MAX_CHUNK_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 64

# 按字符估算中文 Token 数（中文 1 字 ≈ 0.5-0.8 token，取保守值 0.6）
_CHAR_PER_TOKEN_CN = 1.67  # 1 token ≈ 1.67 中文字符
_CHAR_PER_TOKEN_EN = 4.0   # 1 token ≈ 4 英文字符

# 句末边界模式
_SENTENCE_END = re.compile(r"[。！？.!?\n]")


def _estimate_tokens(text: str) -> int:
    """基于字符类型的 Token 数快速估算。

    不使用 tiktoken（避免额外依赖），混合中英文按不同比例估算。
    误差在 ±15% 以内，对于分块粒度控制足够。

    Args:
        text: 输入文本

    Returns:
        估算 token 数
    """
    cn_chars = sum(1 for c in text if "一" <= c <= "鿿" or "　" <= c <= "〿")
    en_chars = len(text) - cn_chars
    return int(cn_chars / _CHAR_PER_TOKEN_CN + en_chars / _CHAR_PER_TOKEN_EN)


def _extract_frontmatter(text: str) -> tuple[dict, str]:
    """从 Markdown 文本中分离 YAML Frontmatter。

    Args:
        text: 原始 Markdown 文本

    Returns:
        (metadata_dict, body_text) 元组
    """
    if not text.startswith("---"):
        return {}, text

    # 找到第二个 ---
    end_idx = text.find("---", 3)
    if end_idx == -1:
        return {}, text

    fm_text = text[3:end_idx].strip()
    body = text[end_idx + 3:].strip()

    metadata: dict = {}
    if fm_text:
        try:
            import yaml
            parsed = yaml.safe_load(fm_text)
            if isinstance(parsed, dict):
                metadata = parsed
        except Exception:
            # Frontmatter 解析失败，保留原始文本作为元数据
            metadata = {"_raw_frontmatter": fm_text}

    return metadata, body


def _source_from_dir(file_path: Path, project_root: Path) -> ChunkSource:
    """根据文件路径推断分块来源。

    Args:
        file_path: 文件路径
        project_root: 项目根目录

    Returns:
        对应的 ChunkSource 枚举值
    """
    try:
        rel = file_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        # 文件不在项目目录下，默认设为 DRAFT
        return ChunkSource.DRAFT

    parts = rel.parts
    if len(parts) == 0:
        return ChunkSource.DRAFT

    top_dir = parts[0].lower()
    if top_dir == "canon":
        return ChunkSource.CANON
    elif top_dir == "characters":
        return ChunkSource.CHARACTER
    elif top_dir == "subconscious":
        return ChunkSource.SUBCONSCIOUS
    elif top_dir == "draft":
        return ChunkSource.DRAFT
    else:
        return ChunkSource.DRAFT


def _split_by_heading_level(
    text: str,
    level: int,
    max_tokens: int,
) -> list[str]:
    """按指定级别的 Markdown 标题递归切分文本。

    Args:
        text: 待切分文本
        level: 标题级别（1-6）
        max_tokens: 每个分段的最大 Token 数

    Returns:
        切分后的文本段列表
    """
    if level > 6:
        return [text]

    # 构建标题匹配模式
    prefix = "#" * level
    # 匹配行首的标题（前面不能有更多 #）
    pattern = re.compile(rf"^{prefix}\s+(.+)$", re.MULTILINE)

    matches = list(pattern.finditer(text))
    if len(matches) <= 1:
        # 当前级别没有足够标题，尝试下一级
        return _split_by_heading_level(text, level + 1, max_tokens)

    segments: list[str] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[start:end].strip()
        if segment:
            # 当前段超限则递归下一级
            if _estimate_tokens(segment) > max_tokens and level < 6:
                segments.extend(
                    _split_by_heading_level(segment, level + 1, max_tokens)
                )
            else:
                segments.append(segment)
    return segments


def _split_by_blank_lines(text: str, max_tokens: int) -> list[str]:
    """按空行分隔段落切分超限文本。

    Args:
        text: 待切分文本
        max_tokens: 每个分段的最大 Token 数

    Returns:
        切分后的文本段列表
    """
    paragraphs = re.split(r"\n\s*\n", text)
    result: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        combined = current + "\n\n" + para if current else para
        if _estimate_tokens(combined) > max_tokens and current:
            result.append(current)
            current = para
        else:
            current = combined

    if current:
        result.append(current)

    return result if result else [text]


def _split_by_sentence(text: str, max_tokens: int) -> list[str]:
    """按句末边界切分超限文本（最后的兜底策略）。

    Args:
        text: 待切分文本
        max_tokens: 每个分段的最大 Token 数

    Returns:
        切分后的文本段列表
    """
    sentences = _SENTENCE_END.split(text)
    result: list[str] = []
    current = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        combined = current + sent if current else sent
        if _estimate_tokens(combined) > max_tokens and current:
            result.append(current)
            current = sent
        else:
            current = combined

    if current:
        result.append(current)

    return result if result else [text]


def chunk_text(
    text: str,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
) -> list[str]:
    """将一段 Markdown 文本递归切分为多个 chunk。

    切分层级：一级标题 → 二级标题 → ... → 六级标题 → 空行段落 → 句末边界

    Args:
        text: 待切分的 Markdown 文本
        max_tokens: 每个 chunk 的最大 Token 数

    Returns:
        切分后的文本段列表
    """
    if not text.strip():
        return []

    # 尝试按一级标题切分
    segments = _split_by_heading_level(text, level=1, max_tokens=max_tokens)

    # 对仍超限的段继续切分
    final: list[str] = []
    for seg in segments:
        if _estimate_tokens(seg) <= max_tokens:
            final.append(seg)
        else:
            # 按空行切分
            sub = _split_by_blank_lines(seg, max_tokens)
            for s in sub:
                if _estimate_tokens(s) <= max_tokens:
                    final.append(s)
                else:
                    # 按句子切分（兜底）
                    final.extend(_split_by_sentence(s, max_tokens))

    return final


class MarkdownChunker:
    """Markdown 递归分块器。

    扫描项目目录中的 Markdown 文件，按标题层级递归切分为固定大小的 chunk。
    每个 chunk 携带确定性 chunk_id 和来源标签。

    使用方式:
        chunker = MarkdownChunker(project_root)
        chunks = chunker.chunk_directory(project_root / "canon")
        chunks += chunker.chunk_file(project_root / "draft" / "ch_001.md")
    """

    def __init__(
        self,
        project_root: Path,
        max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        """初始化分块器。

        Args:
            project_root: 项目根目录（用于推断 ChunkSource）
            max_chunk_tokens: 每个 chunk 的最大 Token 数
            overlap_tokens: 相邻 chunk 的重叠 Token 数（保留字段，当前未实现）
        """
        self.project_root = project_root
        self.max_chunk_tokens = max_chunk_tokens
        self.overlap_tokens = overlap_tokens

    def chunk_file(self, file_path: Path) -> list[Chunk]:
        """将单个 Markdown 文件切分为 chunk 列表。

        Args:
            file_path: Markdown 文件路径

        Returns:
            Chunk 对象列表
        """
        if not file_path.exists() or not file_path.suffix == ".md":
            return []

        source = _source_from_dir(file_path, self.project_root)
        doc_stem = file_path.stem

        try:
            raw_text = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取文件失败: %s (%s)", file_path, e)
            return []

        metadata, body = _extract_frontmatter(raw_text)

        segments = chunk_text(body, self.max_chunk_tokens)

        chunks: list[Chunk] = []
        for i, seg in enumerate(segments):
            chunk_id = f"{source.value}_{doc_stem}_p{i}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    source=source,
                    doc_stem=doc_stem,
                    chunk_index=i,
                    text=seg,
                    metadata={**metadata},
                )
            )

        return chunks

    def chunk_directory(self, directory: Path, recursive: bool = True) -> list[Chunk]:
        """扫描目录下所有 Markdown 文件并切分。

        Args:
            directory: 目标目录
            recursive: 是否递归扫描子目录

        Returns:
            所有切分产出的 Chunk 列表
        """
        if not directory.exists():
            logger.warning("目录不存在: %s", directory)
            return []

        pattern = "**/*.md" if recursive else "*.md"
        all_chunks: list[Chunk] = []

        for md_file in sorted(directory.glob(pattern)):
            chunks = self.chunk_file(md_file)
            all_chunks.extend(chunks)

        logger.info(
            "目录 %s 分块完成: %d 个文件 → %d 个 chunk",
            directory.name,
            sum(1 for _ in directory.glob(pattern)),
            len(all_chunks),
        )
        return all_chunks

    def chunk_project_sources(
        self,
        canon_dir: Path | None = None,
        characters_dir: Path | None = None,
        subconscious_dir: Path | None = None,
        draft_dir: Path | None = None,
    ) -> dict[ChunkSource, list[Chunk]]:
        """按来源分组的项目全量分块。

        扫描四个源目录，按 ChunkSource 分组返回结果。

        Args:
            canon_dir: canon/ 目录路径
            characters_dir: characters/ 目录路径
            subconscious_dir: subconscious/ 目录路径
            draft_dir: draft/ 目录路径

        Returns:
            {ChunkSource: [Chunk, ...]} 分组字典
        """
        result: dict[ChunkSource, list[Chunk]] = {}

        dirs: list[tuple[Path | None, ChunkSource]] = [
            (canon_dir, ChunkSource.CANON),
            (characters_dir, ChunkSource.CHARACTER),
            (subconscious_dir, ChunkSource.SUBCONSCIOUS),
            (draft_dir, ChunkSource.DRAFT),
        ]

        for directory, source in dirs:
            if directory is not None and directory.exists():
                chunks = self.chunk_directory(directory)
                if chunks:
                    result[source] = chunks

        return result
