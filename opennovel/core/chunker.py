"""Markdown 递归分块器 — 按文档结构层级分块，保证语义完整性。

分块策略（ADR 0007）：
1. `#` 一级标题 → 顶级边界
2. 超 max_chunk_tokens=512 → `##` 二级标题切割
3. 仍超限 → 空行段落切割
4. 仍超限 → 句末边界切割

Chunk ID 生成规则（确定性，不含 hash）：
    {source}_{doc_stem}_p{index}
    例如：canon_world-rules_p003

Overlap：相邻分块间重叠 64 tokens，保证上下文的连续性。

使用方式:
    chunker = MarkdownChunker()
    chunks = chunker.chunk_document(
        text="# 第一章\\n\\n内容...",
        source=ChunkSource.DRAFT,
        doc_stem="ch_001",
    )
    for chunk in chunks:
        print(chunk.chunk_id, chunk.text[:50])
"""

import logging
import re
from pathlib import Path

import tiktoken

from opennovel.schemas.search import Chunk, ChunkSource

logger = logging.getLogger(__name__)

_DEFAULT_ENCODING = "cl100k_base"
_MAX_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64

# 按文档类型的动态 chunk 大小（P2 支撑长篇）
# 数值为最大 token 数
_SOURCE_CHUNK_SIZES: dict[ChunkSource, int] = {
    ChunkSource.CANON: 384,
    ChunkSource.CHARACTER: 0,  # 0 表示整卡一个 chunk（特殊处理）
    ChunkSource.DRAFT: 1024,
    ChunkSource.SUBCONSCIOUS: 256,
}

# 句子结束边界正则（中文句号、问号、感叹号，英文句点+空格）
# 注意：不含 \n，换行已在段落级处理
_SENTENCE_BOUNDARY = re.compile(r"([。！？]|\.\s)")
# Markdown 一级标题正则
_H1_PATTERN = re.compile(r"^# ", re.MULTILINE)
# Markdown 二级标题正则
_H2_PATTERN = re.compile(r"^## ", re.MULTILINE)


class MarkdownChunker:
    """Markdown 递归分块器。

    按文档层级（H1 → H2 → 段落 → 句子）递归切割，
    保证每个分块的语义完整性和 Token 预算控制。
    """

    def __init__(
        self,
        max_chunk_tokens: int = _MAX_CHUNK_TOKENS,
        overlap_tokens: int = _OVERLAP_TOKENS,
        encoding_model: str = _DEFAULT_ENCODING,
    ) -> None:
        """初始化分块器。

        Args:
            max_chunk_tokens: 每块最大 Token 数，默认 512
            overlap_tokens: 相邻块重叠 Token 数，默认 64
            encoding_model: tiktoken 编码模型名
        """
        self.max_chunk_tokens = max_chunk_tokens
        self.overlap_tokens = overlap_tokens
        try:
            self._encoding = tiktoken.get_encoding(encoding_model)
        except (KeyError, ValueError):
            logger.warning("未找到编码模型 %s，回退到 cl100k_base", encoding_model)
            self._encoding = tiktoken.get_encoding(_DEFAULT_ENCODING)

    def count_tokens(self, text: str) -> int:
        """计算文本的 Token 数量。

        Args:
            text: 待计算的文本

        Returns:
            Token 数量
        """
        return len(self._encoding.encode(text))

    def chunk_document(
        self,
        text: str,
        source: ChunkSource,
        doc_stem: str,
        metadata: dict | None = None,
    ) -> list[Chunk]:
        """将整篇文档分块。

        Args:
            text: 文档正文
            source: 文档的来源类型（权威层级）
            doc_stem: 文档标识的短名称，如 "ch_001"、"world-rules"
            metadata: 附加元数据，如 chapter_id、character_id 等

        Returns:
            分块列表，按原文顺序排列
        """
        if not text or not text.strip():
            return []

        # 动态 chunk 大小（P2）
        source_max_tokens = _SOURCE_CHUNK_SIZES.get(source, self.max_chunk_tokens)
        if source == ChunkSource.CHARACTER:
            # 角色卡：整卡一个 chunk
            return [
                Chunk(
                    chunk_id=self._make_chunk_id(source.value, doc_stem, 0),
                    text=text.strip(),
                    source=source,
                    metadata=dict(metadata or {}),
                )
            ]

        meta = dict(metadata or {})
        meta["doc_stem"] = doc_stem
        meta["max_chunk_tokens"] = source_max_tokens

        # 剥离 YAML Frontmatter（--- 之间的元数据块），单独索引
        body = text
        fm_text = ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm_text = parts[1].strip()
                body = parts[2].strip()

        chunks: list[Chunk] = []
        global_index = 0

        # Frontmatter 单独作为 metadata chunk 索引（确保 id/name 可检索）
        if fm_text:
            chunks.append(
                Chunk(
                    chunk_id=self._make_chunk_id(source.value, doc_stem, global_index),
                    text=f"[metadata]\n{fm_text}",
                    source=source,
                    metadata={**meta, "type": "frontmatter"},
                )
            )
            global_index += 1

        # 正文按 H1 分割为顶级块
        segments = self._split_by_h1(body) if body else []

        for segment in segments:
            seg_chunks = self._recursive_chunk(
                text=segment,
                source=source,
                meta=meta,
                start_index=global_index,
                depth=0,
            )
            chunks.extend(seg_chunks)
            global_index += len(seg_chunks)

        return chunks

    def chunk_file(
        self,
        file_path: Path,
        source: ChunkSource,
        metadata: dict | None = None,
    ) -> list[Chunk]:
        """从文件路径读取并分块。

        Args:
            file_path: Markdown 文件路径
            source: 文档的来源类型
            metadata: 附加元数据

        Returns:
            分块列表
        """
        doc_stem = file_path.stem
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取文件失败: %s — %s", file_path, e)
            return []
        return self.chunk_document(text, source, doc_stem, metadata)

    # ── 内部递归分块 ──────────────────────────────────────────────

    def _recursive_chunk(
        self,
        text: str,
        source: ChunkSource,
        meta: dict,
        start_index: int = 0,
        depth: int = 0,
    ) -> list[Chunk]:
        """递归分块核心逻辑。

        层级递进：H1（depth=0）→ H2（depth=1）→ 段落（depth=2）→ 句子（depth=3）。

        Args:
            text: 待分块的文本
            source: 来源类型
            meta: 元数据
            start_index: 起始分块序号
            depth: 当前递归深度

        Returns:
            分块列表
        """
        max_chunk_tokens = meta.get("max_chunk_tokens", self.max_chunk_tokens)
        token_count = self.count_tokens(text)

        # 基线条件：未超限，直接作为一个分块
        if token_count <= max_chunk_tokens:
            chunk_id = self._make_chunk_id(source.value, meta.get("doc_stem", "doc"), start_index)
            return [
                Chunk(
                    chunk_id=chunk_id,
                    text=text.strip(),
                    source=source,
                    metadata=dict(meta),
                )
            ]

        # 递归条件：超限，按当前层级的粒度切割
        # depth=0: H1 段落 → 按 H2 切割
        # depth=1: H2 段落 → 按空行（段落）切割
        # depth=2: 段落 → 按句子切割
        # depth=3: 句子 → 强制均分（最后的保险）
        if depth == 0:
            parts = self._split_by_h2(text)
        elif depth == 1:
            parts = self._split_by_paragraph(text)
        elif depth == 2:
            parts = self._split_by_sentence(text)
        else:
            # depth >= 3: 强制按 Token 均分
            parts = self._split_by_token_force(text)

        # 对每个子部分递归分块
        chunks: list[Chunk] = []
        local_index = 0
        for parts_index, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue

            sub_chunks = self._recursive_chunk(
                text=part,
                source=source,
                meta=meta,
                start_index=start_index + local_index,
                depth=depth + 1,
            )
            chunks.extend(sub_chunks)
            local_index += len(sub_chunks)

            # 在相邻块之间增加重叠（从当前块的末尾截取 overlap_tokens 追加到下一块）
            if sub_chunks and parts_index + 1 < len(parts):
                last_text = sub_chunks[-1].text
                overlap_text = self._extract_overlap(last_text)
                if overlap_text:
                    # 直接在原始 parts 列表中修改下一个元素（避免切片副本丢失修改）
                    parts[parts_index + 1] = overlap_text + "\n" + parts[parts_index + 1]

        return chunks

    # ── 切割策略 ──────────────────────────────────────────────────

    def _split_by_h1(self, text: str) -> list[str]:
        """按 H1 标题分割文本。

        Args:
            text: 原始文本

        Returns:
            按 H1 分割后的文本段列表
        """
        matches = list(_H1_PATTERN.finditer(text))
        if len(matches) <= 1:
            return [text]

        parts: list[str] = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            parts.append(text[start:end].strip())

        return [p for p in parts if p]

    def _split_by_h2(self, text: str) -> list[str]:
        """按 H2 标题（##）分割文本。

        Args:
            text: 文本

        Returns:
            分割后的文本段列表
        """
        matches = list(_H2_PATTERN.finditer(text))
        if len(matches) <= 1:
            return [text]

        parts: list[str] = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            parts.append(text[start:end].strip())

        return [p for p in parts if p]

    def _split_by_paragraph(self, text: str) -> list[str]:
        """按段落（连续空行或两个换行）分割文本。

        Args:
            text: 文本

        Returns:
            段落列表
        """
        # 按两个以上换行符分割
        paragraphs = re.split(r"\n\s*\n", text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _split_by_sentence(self, text: str) -> list[str]:
        """按句子边界（。！？等）分割文本。

        Args:
            text: 文本

        Returns:
            句子列表
        """
        # 在句子边界处分隔，保留分隔符
        parts = _SENTENCE_BOUNDARY.split(text)
        # 重组：将分隔符合并回前面的句子
        sentences: list[str] = []
        buffer = ""
        for part in parts:
            if _SENTENCE_BOUNDARY.fullmatch(part):
                buffer += part
            else:
                if buffer:
                    sentences.append(buffer.strip())
                buffer = part
        if buffer.strip():
            sentences.append(buffer.strip())

        return [s for s in sentences if s]

    def _split_by_token_force(self, text: str) -> list[str]:
        """强制按 Token 均分（最后保险）。

        Args:
            text: 文本

        Returns:
            均分后的文本段
        """
        tokens = self._encoding.encode(text)
        half = len(tokens) // 2
        if half <= 0:
            return [text]

        # 尝试在接近一半 Token 的位置的单词/字符边界分割
        first_half = self._encoding.decode(tokens[:half])
        second_half = self._encoding.decode(tokens[half:])
        return [first_half.strip(), second_half.strip()]

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _extract_overlap(self, text: str) -> str:
        """从文本末尾提取重叠 Token 的内容。

        Args:
            text: 文本

        Returns:
            末尾 overlap_tokens 对应的文本
        """
        tokens = self._encoding.encode(text)
        if len(tokens) <= self.overlap_tokens:
            return ""

        overlap_tokens_list = tokens[-self.overlap_tokens :]
        return self._encoding.decode(overlap_tokens_list).strip()

    @staticmethod
    def _make_chunk_id(source: str, doc_stem: str, index: int) -> str:
        """生成确定性的分块 ID。

        Args:
            source: 来源类型标识
            doc_stem: 文档短名
            index: 分块序号（从 0 开始）

        Returns:
            格式化的分块 ID：{source}_{doc_stem}_p{index:04d}
        """
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", doc_stem)
        return f"{source}_{safe_stem}_p{index:04d}"

    @staticmethod
    def parse_chunk_id(chunk_id: str) -> dict | None:
        """解析分块 ID 为结构化信息。

        Args:
            chunk_id: 分块 ID，如 "canon_world-rules_p0003"

        Returns:
            解析结果字典，含 source、doc_stem、index；解析失败返回 None
        """
        pattern = r"^([a-z_]+)_([a-zA-Z0-9_-]+)_p(\d{4,})$"
        match = re.match(pattern, chunk_id)
        if not match:
            return None
        return {
            "source": match.group(1),
            "doc_stem": match.group(2),
            "index": int(match.group(3)),
        }
