"""Markdown 文本切分器 - 纯文本处理，不涉及文件 I/O。

职责：
- 将 Markdown 正文按场景（`#` 标题）切分为语义块
- 基于 tiktoken 控制每个 Chunk 的 Token 预算
- 为 Phase 2 的三级上下文策略提供文本预处理支持

与 storage/yaml_storage.py 的分工：
- 本模块不碰文件系统，只处理内存中的文本字符串
- 文件 I/O 全部由 YAMLStorage 负责
"""

import logging

import tiktoken

logger = logging.getLogger(__name__)


_DEFAULT_ENCODING = "cl100k_base"


def split_chapter_into_scenes(
    markdown_text: str,
    max_tokens: int = 2000,
    encoding_model: str = _DEFAULT_ENCODING,
) -> list[str]:
    """将章节正文按 '# ' 标题切分为场景块，每个块不超过 max_tokens。

    适用于 Phase 2 全景模式的历史正文倒序灌注，以及 FRUGAL 模式的 RAG 分块。

    Args:
        markdown_text: 章节正文（不含 Frontmatter）
        max_tokens: 每个场景块的最大 Token 数，默认 2000
        encoding_model: tiktoken 编码模型名，默认 cl100k_base

    Returns:
        场景文本列表，按原文顺序排列
    """
    try:
        encoding = tiktoken.get_encoding(encoding_model)
    except (KeyError, ValueError):
        logger.warning("未找到编码模型 %s，回退到 cl100k_base", encoding_model)
        encoding = tiktoken.get_encoding(_DEFAULT_ENCODING)

    if not markdown_text or not markdown_text.strip():
        return []

    lines = markdown_text.split("\n")
    scenes: list[str] = []
    current_scene_lines: list[str] = []
    current_tokens = 0

    for line in lines:
        line_tokens = len(encoding.encode(line))
        is_heading = line.strip().startswith("# ")

        # 遇到新标题且当前场景不为空，结束当前场景
        if is_heading and current_scene_lines:
            scenes.append("\n".join(current_scene_lines))
            current_scene_lines = []
            current_tokens = 0

        # 如果当前行加入后会超限，先截断当前场景
        if current_tokens + line_tokens > max_tokens and current_scene_lines:
            scenes.append("\n".join(current_scene_lines))
            current_scene_lines = []
            current_tokens = 0

        current_scene_lines.append(line)
        current_tokens += line_tokens

    # 剩余内容
    if current_scene_lines:
        scenes.append("\n".join(current_scene_lines))

    return scenes


def count_text_tokens(
    text: str,
    encoding_model: str = _DEFAULT_ENCODING,
) -> int:
    """计算文本的 Token 数量。

    Args:
        text: 待计算的文本
        encoding_model: tiktoken 编码模型名

    Returns:
        Token 数量
    """
    try:
        encoding = tiktoken.get_encoding(encoding_model)
    except (KeyError, ValueError):
        encoding = tiktoken.get_encoding(_DEFAULT_ENCODING)
    return len(encoding.encode(text))


def truncate_to_budget(
    text: str,
    max_tokens: int,
    encoding_model: str = _DEFAULT_ENCODING,
) -> str:
    """将文本截断至指定 Token 预算内。

    Args:
        text: 待截断的文本
        max_tokens: 最大 Token 数
        encoding_model: tiktoken 编码模型名

    Returns:
        截断后的文本
    """
    try:
        encoding = tiktoken.get_encoding(encoding_model)
    except (KeyError, ValueError):
        encoding = tiktoken.get_encoding(_DEFAULT_ENCODING)

    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    truncated_tokens = tokens[:max_tokens]
    return encoding.decode(truncated_tokens)
