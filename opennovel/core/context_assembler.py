"""上下文权威组装器与 Token 熔断。

负责将三层数据按权威分级打包送入 LLM，根据模型窗口大小自动选择策略：

- FRUGAL (<32K): 8K 预算，RAG + 摘要，精打细算
- STANDARD (32K-128K): 48K 预算，当前章全量 + 全部活跃角色状态
- PANORAMIC (>128K): 128K 软限，全量设定 + 全量潜意识 + 历史章节倒序注入

通用入口 `assemble_context()` 为所有 Agent（Actor/Writer/Critic）提供统一的
分级上下文管道，通过 `task_message` 参数区分不同 Agent 的任务指令。

详见 docs/adr/0002-three-tier-context-strategy.md 和 docs/adr/0003-*.md。
"""

import logging
from enum import Enum
from pathlib import Path

import tiktoken

from opennovel.core.parser import split_chapter_into_scenes
from opennovel.core.state_projector import StateProjector
from opennovel.schemas.character import AuthorityLevel
from opennovel.storage.sqlite import EventStore
from opennovel.storage.yaml_storage import YAMLStorage

logger = logging.getLogger(__name__)


# ── 模型窗口映射（用于 detect_strategy） ──

# 常见模型的上下文窗口大小（Token 数）
# 未列出的模型默认返回 128K（STANDARD 策略上限）
_MODEL_WINDOWS: dict[str, int] = {
    # DeepSeek 系列
    "deepseek/deepseek-v4-flash": 800_000,
    "deepseek/deepseek-chat": 64_000,
    "deepseek/deepseek-r1": 100_000,
    # OpenAI 系列
    "gpt-4": 8_192,
    "gpt-4-turbo": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    # Anthropic 系列
    "claude-3-opus": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-8": 200_000,
    # Google 系列
    "gemini/gemini-1.5-pro": 1_000_000,
    "gemini/gemini-1.5-flash": 1_000_000,
}

_DEFAULT_WINDOW = 128_000  # 未知模型默认值（STANDARD 策略上限）


def get_model_window(model_name: str) -> int:
    """根据模型名称获取上下文窗口大小。

    精确匹配优先，未命中时尝试前缀匹配（如 'deepseek/deepseek-chat' 匹配 'deepseek/*'），
    仍未命中返回默认值 128K。

    Args:
        model_name: 模型名称（如 "deepseek/deepseek-v4-flash"）

    Returns:
        上下文窗口 Token 数
    """
    if not model_name:
        return _DEFAULT_WINDOW

    # 精确匹配
    if model_name in _MODEL_WINDOWS:
        return _MODEL_WINDOWS[model_name]

    # 前缀匹配：取 provider 前缀的最长匹配
    provider = model_name.split("/")[0] if "/" in model_name else model_name
    provider_match = f"{provider}/"
    for key in sorted(_MODEL_WINDOWS.keys(), reverse=True):
        if key.startswith(provider_match):
            return _MODEL_WINDOWS[key]

    logger.debug("未知模型 %s，使用默认窗口 %d", model_name, _DEFAULT_WINDOW)
    return _DEFAULT_WINDOW


# ── 三级上下文策略 ──


class ContextStrategy(str, Enum):
    """上下文组装策略，根据模型窗口大小自动映射。"""

    FRUGAL = "frugal"  # <32K: RAG + 摘要
    STANDARD = "standard"  # 32K-128K: 均衡型
    PANORAMIC = "panoramic"  # >128K: 全景沉浸


# 策略阈值（Token 数）
_FRUGAL上限 = 32_000
_STANDARD上限 = 128_000


def detect_strategy(max_window: int) -> ContextStrategy:
    """根据模型上下文窗口大小自动检测上下文策略。

    Args:
        max_window: 模型的最大上下文窗口 Token 数

    Returns:
        对应的上下文策略
    """
    if max_window < _FRUGAL上限:
        return ContextStrategy.FRUGAL
    elif max_window <= _STANDARD上限:
        return ContextStrategy.STANDARD
    else:
        return ContextStrategy.PANORAMIC


# ── Token 预算常量 ──

# FRUGAL 策略预算
TOTAL_TOKEN_BUDGET = 8000
OUTPUT_RESERVE = 2000
INPUT_TOKEN_BUDGET = TOTAL_TOKEN_BUDGET - OUTPUT_RESERVE

# STANDARD 策略预算
STANDARD_TOKEN_BUDGET = 48_000

# PANORAMIC 策略软限（即使模型支持 1M，也限制在 128K 防止延迟失控）
PANORAMIC_SOFT_LIMIT = 128_000

# 各层级预算占比（FRUGAL 模式使用）
BUDGET_RATIOS = {
    AuthorityLevel.CANON: 0.20,
    AuthorityLevel.STATE_MEMORY: 0.30,
    AuthorityLevel.SUBCONSCIOUS: 0.10,
    "recent_text": 0.40,
}

# 权威层级优先级排序（从低到高，裁剪时从最低开始）
TRUNCATION_ORDER = [
    AuthorityLevel.SUBCONSCIOUS,
    AuthorityLevel.STATE_MEMORY,
    AuthorityLevel.CANON,
]


class TokenCounter:
    """Token 计数器，基于 tiktoken 实现精确的 Token 预算计算。"""

    def __init__(self, model: str = "cl100k_base") -> None:
        """初始化 Token 计数器。

        Args:
            model: tiktoken 编码模型名称，默认使用 cl100k_base (GPT-4 系列)
        """
        try:
            self._encoding = tiktoken.get_encoding(model)
        except (KeyError, ValueError):
            logger.warning("未找到编码模型 %s，回退到 cl100k_base", model)
            self._encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        """计算文本的 Token 数量。

        Args:
            text: 待计算的文本

        Returns:
            Token 数量
        """
        return len(self._encoding.encode(text))

    def truncate_to_budget(self, text: str, max_tokens: int) -> str:
        """将文本截断至指定 Token 预算内。

        Args:
            text: 待截断的文本
            max_tokens: 最大 Token 数

        Returns:
            截断后的文本
        """
        tokens = self._encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated_tokens = tokens[:max_tokens]
        return self._encoding.decode(truncated_tokens)


class ContextMessage:
    """上下文消息单元，携带权威层级标签。"""

    def __init__(
        self,
        role: str,
        content: str,
        authority: AuthorityLevel | None = None,
    ) -> None:
        """初始化上下文消息。

        Args:
            role: 消息角色 (system/user/assistant)
            content: 消息内容
            authority: 权威层级标签，None 表示不可裁剪（如人格注入）
        """
        self.role = role
        self.content = content
        self.authority = authority
        self.token_count = 0  # 由组装器填充

    def to_dict(self) -> dict[str, str]:
        """转换为 LLM API 消息格式。"""
        return {"role": self.role, "content": self.content}


def load_prompt(prompt_path: Path) -> str:
    """加载外置 Prompt 文件。

    Args:
        prompt_path: Prompt Markdown 文件路径

    Returns:
        Prompt 文本内容

    Raises:
        FileNotFoundError: Prompt 文件不存在
    """
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def wrap_with_authority_tag(content: str, authority: AuthorityLevel) -> str:
    """为内容包裹权威层级标签。

    LLM 必须严格遵守冲突降级逻辑：Canon > State > Memory > Subconscious。

    Args:
        content: 原始内容
        authority: 权威层级

    Returns:
        带标签的内容文本
    """
    tag_map = {
        AuthorityLevel.CANON: "[CANON | IMMUTABLE | HIGH AUTHORITY]",
        AuthorityLevel.STATE_MEMORY: "[STATE MEMORY | MEDIUM AUTHORITY]",
        AuthorityLevel.SUBCONSCIOUS: "[SUBCONSCIOUS FRAGMENT | LOW AUTHORITY | OPTIONAL]",
    }
    tag = tag_map.get(authority, "")
    return f"{tag}\n{content}"


# ── 公共入口 ──


def assemble_actor_context(
    chapter_path: Path,
    project_root: Path,
    current_text: str,
    prompt_path: Path | None = None,
    canon_content: str = "",
    subconscious_content: str = "",
    yaml_storage: YAMLStorage | None = None,
    strategy: ContextStrategy = ContextStrategy.FRUGAL,
) -> list[dict[str, str]]:
    """组装 Actor 代理的完整上下文，带 Token 熔断与权威分级。

    根据 strategy 参数选择不同的组装路径：
    - FRUGAL: 固定 8K 预算，按比例分配各层级
    - STANDARD: 48K 预算，注入全部活跃角色状态
    - PANORAMIC: 128K 软限，全量设定 + 全量潜意识

    Args:
        chapter_path: 当前章节 Markdown 文件路径
        project_root: 项目根目录路径
        current_text: 当前正文文本（近期续写锚点）
        prompt_path: Actor 人格 Prompt 文件路径
        canon_content: 从检索引擎获取的设定内容
        subconscious_content: 从潜意识池检索的灵感碎片
        yaml_storage: YAML 存储实例
        strategy: 上下文组装策略

    Returns:
        组装完成的消息列表，可直接传入 LLM API
    """
    task_message = f"[Previous Text]\n{current_text}\n\nCONTINUE:"
    return assemble_context(
        project_root=project_root,
        task_message=task_message,
        prompt_path=prompt_path or Path("prompts/actor.v1.md"),
        chapter_path=chapter_path,
        canon_content=canon_content,
        subconscious_content=subconscious_content,
        yaml_storage=yaml_storage,
        strategy=strategy,
    )


def assemble_context(
    *,
    project_root: Path,
    task_message: str,
    prompt_path: Path,
    chapter_path: Path | None = None,
    canon_content: str = "",
    subconscious_content: str = "",
    causal_chain_context: str = "",
    active_characters: list[str] | None = None,
    yaml_storage: YAMLStorage | None = None,
    strategy: ContextStrategy = ContextStrategy.STANDARD,
) -> list[dict[str, str]]:
    """通用上下文组装入口，为所有 Agent 提供统一的分级上下文管道。

    复用 CANON / STATE_MEMORY / SUBCONSCIOUS 三层权威注入、Token 熔断、
    策略路由（FRUGAL/STANDARD/PANORAMIC）。

    与 assemble_actor_context 的区别：
    - task_message 参数化，不再硬编码 CONTINUE:
    - active_characters 可显式传入（不依赖 chapter_path 的 Frontmatter）
    - prompt_path 必须显式指定
    - causal_chain_context 注入因果链上下文（Phase 2.1）

    Args:
        project_root: 项目根目录路径
        task_message: 最终的 user 消息（Writer 的大纲+创作指令 / Critic 的评审指令等）
        prompt_path: Agent 人格 Prompt 文件路径
        chapter_path: 当前章节路径（可选，用于提取活跃角色和历史章节注入）
        canon_content: 从检索引擎获取的设定内容
        subconscious_content: 从潜意识池检索的灵感碎片
        causal_chain_context: 因果链上下文文本（格式化的事件因果关系）
        active_characters: 显式指定的角色 ID 列表（优先于从 chapter_path 提取）
        yaml_storage: YAML 存储实例
        strategy: 上下文组装策略

    Returns:
        组装完成的消息列表，可直接传入 LLM API
    """
    if strategy == ContextStrategy.PANORAMIC:
        return _assemble_panoramic(
            chapter_path,
            project_root,
            task_message,
            prompt_path,
            canon_content,
            subconscious_content,
            causal_chain_context,
            active_characters,
            yaml_storage,
        )
    elif strategy == ContextStrategy.STANDARD:
        return _assemble_standard(
            chapter_path,
            project_root,
            task_message,
            prompt_path,
            canon_content,
            subconscious_content,
            causal_chain_context,
            active_characters,
            yaml_storage,
        )
    else:
        return _assemble_frugal(
            chapter_path,
            project_root,
            task_message,
            prompt_path,
            canon_content,
            subconscious_content,
            causal_chain_context,
            active_characters,
            yaml_storage,
        )


# ── FRUGAL 策略 ──


def _assemble_frugal(
    chapter_path: Path | None,
    project_root: Path,
    task_message: str,
    prompt_path: Path,
    canon_content: str,
    subconscious_content: str,
    causal_chain_context: str,
    active_characters: list[str] | None,
    yaml_storage: YAMLStorage | None,
) -> list[dict[str, str]]:
    """FRUGAL 策略：固定 8K 预算，按比例分配各层级。"""
    counter = TokenCounter()
    messages: list[ContextMessage] = []
    total_tokens = 0

    # 1. 人格注入 (最高优先级，不可裁剪)
    if prompt_path and prompt_path.exists():
        prompt_text = load_prompt(prompt_path)
        msg = ContextMessage(role="system", content=prompt_text, authority=None)
        msg.token_count = counter.count(prompt_text)
        messages.append(msg)
        total_tokens += msg.token_count

    # 2. 设定 (CANON | IMMUTABLE)
    canon_budget = int(INPUT_TOKEN_BUDGET * BUDGET_RATIOS[AuthorityLevel.CANON])
    if canon_content:
        canon_text = wrap_with_authority_tag(canon_content, AuthorityLevel.CANON)
        canon_tokens = counter.count(canon_text)
        if canon_tokens > canon_budget:
            canon_text = counter.truncate_to_budget(canon_text, canon_budget)
            canon_tokens = canon_budget
        msg = ContextMessage(role="system", content=canon_text, authority=AuthorityLevel.CANON)
        msg.token_count = canon_tokens
        messages.append(msg)
        total_tokens += canon_tokens

    # 3. 角色状态 (STATE MEMORY | MEDIUM) — POV 角色或显式指定的角色
    state_budget = int(INPUT_TOKEN_BUDGET * BUDGET_RATIOS[AuthorityLevel.STATE_MEMORY])
    storage = yaml_storage or YAMLStorage()
    chars_to_load = active_characters or []
    if not chars_to_load and chapter_path:
        pov_id = storage.extract_pov_character_id(chapter_path)
        if pov_id:
            chars_to_load = [pov_id]
    for char_id in chars_to_load[:1]:  # FRUGAL 只取第一个角色
        char_path = project_root / "characters" / f"{char_id}.md"
        if char_path.exists():
            try:
                char_file = storage.read_character_file(char_path)
            except Exception as e:
                logger.warning("读取角色文件失败 %s: %s", char_path, e)
                continue
            state_text = wrap_with_authority_tag(
                char_file.frontmatter.model_dump_json(indent=2),
                AuthorityLevel.STATE_MEMORY,
            )
            state_tokens = counter.count(state_text)
            if state_tokens > state_budget:
                state_text = counter.truncate_to_budget(state_text, state_budget)
                state_tokens = state_budget
            msg = ContextMessage(
                role="system", content=state_text, authority=AuthorityLevel.STATE_MEMORY
            )
            msg.token_count = state_tokens
            messages.append(msg)
            total_tokens += state_tokens

    # 4. 潜意识 (SUBCONSCIOUS | LOW)
    sub_budget = int(INPUT_TOKEN_BUDGET * BUDGET_RATIOS[AuthorityLevel.SUBCONSCIOUS])
    if subconscious_content:
        sub_text = wrap_with_authority_tag(subconscious_content, AuthorityLevel.SUBCONSCIOUS)
        sub_tokens = counter.count(sub_text)
        if sub_tokens > sub_budget:
            sub_text = counter.truncate_to_budget(sub_text, sub_budget)
            sub_tokens = sub_budget
        msg = ContextMessage(role="system", content=sub_text, authority=AuthorityLevel.SUBCONSCIOUS)
        msg.token_count = sub_tokens
        messages.append(msg)
        total_tokens += sub_tokens

    # 4.5 因果链上下文 (STATE MEMORY | MEDIUM) — Phase 2.1
    if causal_chain_context:
        chain_text = wrap_with_authority_tag(
            f"[CAUSAL CHAIN]\n{causal_chain_context}", AuthorityLevel.STATE_MEMORY
        )
        chain_tokens = counter.count(chain_text)
        chain_budget = int(INPUT_TOKEN_BUDGET * 0.10)  # 分配 10% 预算
        if chain_tokens > chain_budget:
            chain_text = counter.truncate_to_budget(chain_text, chain_budget)
            chain_tokens = chain_budget
        msg = ContextMessage(
            role="system", content=chain_text, authority=AuthorityLevel.STATE_MEMORY
        )
        msg.token_count = chain_tokens
        messages.append(msg)
        total_tokens += chain_tokens

    # 5. 任务消息（替代硬编码的 CONTINUE:）
    task_budget = int(INPUT_TOKEN_BUDGET * BUDGET_RATIOS["recent_text"])
    remaining_budget = INPUT_TOKEN_BUDGET - total_tokens
    actual_task_budget = min(task_budget, remaining_budget)
    if actual_task_budget > 0 and task_message:
        task_tokens = counter.count(task_message)
        if task_tokens > actual_task_budget:
            task_message = counter.truncate_to_budget(task_message, actual_task_budget)
        msg = ContextMessage(role="user", content=task_message)
        msg.token_count = counter.count(task_message)
        messages.append(msg)

    # 熔断检查
    messages = _apply_circuit_breaker(messages, counter, INPUT_TOKEN_BUDGET)

    return [m.to_dict() for m in messages]


# ── STANDARD 策略 ──


def _assemble_standard(
    chapter_path: Path | None,
    project_root: Path,
    task_message: str,
    prompt_path: Path,
    canon_content: str,
    subconscious_content: str,
    causal_chain_context: str,
    active_characters: list[str] | None,
    yaml_storage: YAMLStorage | None,
) -> list[dict[str, str]]:
    """STANDARD 策略：48K 预算，注入全部活跃角色状态。

    与 FRUGAL 的区别：
    - 预算从 8K 提升到 48K
    - 注入所有 active_characters 的状态（不仅是 POV）
    - 设定和潜意识分配更多空间
    """
    counter = TokenCounter()
    messages: list[ContextMessage] = []
    total_tokens = 0
    budget = STANDARD_TOKEN_BUDGET - OUTPUT_RESERVE

    # 1. 人格注入 (不可裁剪)
    if prompt_path and prompt_path.exists():
        prompt_text = load_prompt(prompt_path)
        msg = ContextMessage(role="system", content=prompt_text, authority=None)
        msg.token_count = counter.count(prompt_text)
        messages.append(msg)
        total_tokens += msg.token_count

    # 2. 设定 (CANON) — 分配 20% 预算
    canon_budget = int(budget * 0.20)
    if canon_content:
        canon_text = wrap_with_authority_tag(canon_content, AuthorityLevel.CANON)
        canon_tokens = counter.count(canon_text)
        if canon_tokens > canon_budget:
            canon_text = counter.truncate_to_budget(canon_text, canon_budget)
            canon_tokens = canon_budget
        msg = ContextMessage(role="system", content=canon_text, authority=AuthorityLevel.CANON)
        msg.token_count = canon_tokens
        messages.append(msg)
        total_tokens += canon_tokens

    # 3. 所有活跃角色状态 (STATE MEMORY) — 分配 30% 预算
    state_budget = int(budget * 0.30)
    storage = yaml_storage or YAMLStorage()
    chars_to_load = active_characters or []
    if not chars_to_load and chapter_path:
        chars_to_load = _extract_active_characters(chapter_path, storage)
    state_used = 0

    for char_id in chars_to_load:
        if state_used >= state_budget:
            break
        char_path = project_root / "characters" / f"{char_id}.md"
        if not char_path.exists():
            continue
        try:
            char_file = storage.read_character_file(char_path)
        except Exception as e:
            logger.warning("读取角色文件失败 %s: %s", char_path, e)
            continue
        state_text = wrap_with_authority_tag(
            char_file.frontmatter.model_dump_json(indent=2),
            AuthorityLevel.STATE_MEMORY,
        )
        state_tokens = counter.count(state_text)
        remaining = state_budget - state_used
        if state_tokens > remaining:
            state_text = counter.truncate_to_budget(state_text, remaining)
            state_tokens = remaining
        msg = ContextMessage(
            role="system", content=state_text, authority=AuthorityLevel.STATE_MEMORY
        )
        msg.token_count = state_tokens
        messages.append(msg)
        total_tokens += state_tokens
        state_used += state_tokens

    # 3.5 状态投影快照 (STATE MEMORY) — 可选，使用剩余预算
    _proj_chapter = chapter_path.stem if chapter_path else ""
    if chars_to_load and _proj_chapter:
        try:
            db_path = project_root / ".novel.db"
            if db_path.exists():
                event_store = EventStore(db_path)
                projector = StateProjector(event_store)
                snapshots = []
                for char_id in chars_to_load:
                    snap = projector.project(char_id, _proj_chapter)
                    if snap.event_count > 0:
                        snapshots.append(snap)

                if snapshots:
                    proj_text = projector.format_snapshots(snapshots)
                    proj_text = wrap_with_authority_tag(proj_text, AuthorityLevel.STATE_MEMORY)
                    proj_tokens = counter.count(proj_text)
                    remaining = state_budget - state_used
                    if remaining > 0 and proj_tokens <= remaining:
                        msg = ContextMessage(
                            role="system",
                            content=proj_text,
                            authority=AuthorityLevel.STATE_MEMORY,
                        )
                        msg.token_count = proj_tokens
                        messages.append(msg)
                        total_tokens += proj_tokens
                        state_used += proj_tokens
        except Exception as e:
            logger.debug("状态投影快照注入失败（非关键路径）: %s", e)

    # 4. 潜意识 (SUBCONSCIOUS) — 分配 15% 预算
    sub_budget = int(budget * 0.15)
    if subconscious_content:
        sub_text = wrap_with_authority_tag(subconscious_content, AuthorityLevel.SUBCONSCIOUS)
        sub_tokens = counter.count(sub_text)
        if sub_tokens > sub_budget:
            sub_text = counter.truncate_to_budget(sub_text, sub_budget)
            sub_tokens = sub_budget
        msg = ContextMessage(role="system", content=sub_text, authority=AuthorityLevel.SUBCONSCIOUS)
        msg.token_count = sub_tokens
        messages.append(msg)
        total_tokens += sub_tokens

    # 4.5 因果链上下文 (STATE MEMORY) — 分配 10% 预算 — Phase 2.1
    if causal_chain_context:
        chain_budget = int(budget * 0.10)
        chain_text = wrap_with_authority_tag(
            f"[CAUSAL CHAIN]\n{causal_chain_context}", AuthorityLevel.STATE_MEMORY
        )
        chain_tokens = counter.count(chain_text)
        if chain_tokens > chain_budget:
            chain_text = counter.truncate_to_budget(chain_text, chain_budget)
            chain_tokens = chain_budget
        msg = ContextMessage(
            role="system", content=chain_text, authority=AuthorityLevel.STATE_MEMORY
        )
        msg.token_count = chain_tokens
        messages.append(msg)
        total_tokens += chain_tokens

    # 5. 任务消息 — 使用剩余预算
    remaining_budget = budget - total_tokens
    if remaining_budget > 0 and task_message:
        task_tokens = counter.count(task_message)
        if task_tokens > remaining_budget:
            task_message = counter.truncate_to_budget(task_message, remaining_budget)
        msg = ContextMessage(role="user", content=task_message)
        msg.token_count = counter.count(task_message)
        messages.append(msg)

    # 熔断检查
    messages = _apply_circuit_breaker(messages, counter, budget)

    return [m.to_dict() for m in messages]


# ── PANORAMIC 策略 ──


def _assemble_panoramic(
    chapter_path: Path | None,
    project_root: Path,
    task_message: str,
    prompt_path: Path,
    canon_content: str,
    subconscious_content: str,
    causal_chain_context: str,
    active_characters: list[str] | None,
    yaml_storage: YAMLStorage | None,
) -> list[dict[str, str]]:
    """PANORAMIC 策略：128K 软限，全量设定 + 全量潜意识 + 全部角色。

    与 STANDARD 的区别：
    - 软限 128K（即使模型支持 1M，防止延迟失控）
    - 设定和潜意识不做截断，全量注入
    - 注入所有活跃角色状态
    - 历史章节倒序注入（需要 chapter_path）
    - 因果链全量注入
    """
    counter = TokenCounter()
    messages: list[ContextMessage] = []
    total_tokens = 0
    budget = PANORAMIC_SOFT_LIMIT

    # 1. 人格注入 (不可裁剪)
    if prompt_path and prompt_path.exists():
        prompt_text = load_prompt(prompt_path)
        msg = ContextMessage(role="system", content=prompt_text, authority=None)
        msg.token_count = counter.count(prompt_text)
        messages.append(msg)
        total_tokens += msg.token_count

    # 2. 设定 (CANON) — 全量注入
    if canon_content:
        canon_text = wrap_with_authority_tag(canon_content, AuthorityLevel.CANON)
        canon_tokens = counter.count(canon_text)
        msg = ContextMessage(role="system", content=canon_text, authority=AuthorityLevel.CANON)
        msg.token_count = canon_tokens
        messages.append(msg)
        total_tokens += canon_tokens

    # 3. 所有活跃角色状态 (STATE MEMORY) — 全量注入
    storage = yaml_storage or YAMLStorage()
    chars_to_load = active_characters or []
    if not chars_to_load and chapter_path:
        chars_to_load = _extract_active_characters(chapter_path, storage)

    for char_id in chars_to_load:
        char_path = project_root / "characters" / f"{char_id}.md"
        if not char_path.exists():
            continue
        try:
            char_file = storage.read_character_file(char_path)
        except Exception as e:
            logger.warning("读取角色文件失败 %s: %s", char_path, e)
            continue
        state_text = wrap_with_authority_tag(
            char_file.frontmatter.model_dump_json(indent=2),
            AuthorityLevel.STATE_MEMORY,
        )
        state_tokens = counter.count(state_text)
        msg = ContextMessage(
            role="system", content=state_text, authority=AuthorityLevel.STATE_MEMORY
        )
        msg.token_count = state_tokens
        messages.append(msg)
        total_tokens += state_tokens

    # 4. 潜意识 (SUBCONSCIOUS) — 全量注入
    if subconscious_content:
        sub_text = wrap_with_authority_tag(subconscious_content, AuthorityLevel.SUBCONSCIOUS)
        sub_tokens = counter.count(sub_text)
        msg = ContextMessage(role="system", content=sub_text, authority=AuthorityLevel.SUBCONSCIOUS)
        msg.token_count = sub_tokens
        messages.append(msg)
        total_tokens += sub_tokens

    # 4.5 因果链上下文 (STATE MEMORY) — 全量注入 — Phase 2.1
    if causal_chain_context:
        chain_text = wrap_with_authority_tag(
            f"[CAUSAL CHAIN]\n{causal_chain_context}", AuthorityLevel.STATE_MEMORY
        )
        chain_tokens = counter.count(chain_text)
        msg = ContextMessage(
            role="system", content=chain_text, authority=AuthorityLevel.STATE_MEMORY
        )
        msg.token_count = chain_tokens
        messages.append(msg)
        total_tokens += chain_tokens

    # 5. 历史章节倒序注入 — PANORAMIC 独有（需要 chapter_path）
    remaining_budget = budget - total_tokens
    if remaining_budget > 0 and chapter_path:
        history_text = _load_previous_chapters(
            chapter_path, project_root, counter, remaining_budget
        )
        if history_text:
            msg = ContextMessage(
                role="system",
                content=f"[CHAPTER HISTORY | REVERSE ORDER]\n{history_text}",
            )
            msg.token_count = counter.count(history_text)
            messages.append(msg)
            total_tokens += msg.token_count

    # 6. 任务消息 — 使用剩余预算
    remaining_budget = budget - total_tokens
    if remaining_budget > 0 and task_message:
        task_tokens = counter.count(task_message)
        if task_tokens > remaining_budget:
            task_message = counter.truncate_to_budget(task_message, remaining_budget)
        msg = ContextMessage(role="user", content=task_message)
        msg.token_count = counter.count(task_message)
        messages.append(msg)

    # 熔断检查
    messages = _apply_circuit_breaker(messages, counter, budget)

    return [m.to_dict() for m in messages]


# ── 内部工具函数 ──


def _extract_active_characters(chapter_path: Path, storage: YAMLStorage) -> list[str]:
    """从章节 Frontmatter 提取所有活跃角色 ID 列表。

    包括 POV 角色和 active_characters 列表中的所有角色，去重后返回。
    POV 角色始终排在第一位。

    Args:
        chapter_path: 章节文件路径
        storage: YAML 存储实例

    Returns:
        去重后的角色 ID 列表
    """
    seen: set[str] = set()
    result: list[str] = []

    # POV 角色优先
    pov_id = storage.extract_pov_character_id(chapter_path)
    if pov_id and isinstance(pov_id, str):
        seen.add(pov_id)
        result.append(pov_id)

    # active_characters 列表
    for cid in storage.extract_active_characters(chapter_path):
        if isinstance(cid, str) and cid not in seen:
            seen.add(cid)
            result.append(cid)

    return result


def _load_previous_chapters(
    current_chapter_path: Path,
    project_root: Path,
    counter: TokenCounter,
    budget: int,
) -> str:
    """加载历史章节正文，按倒序注入（最近的章节优先）。

    扫描 draft/ 目录下所有 .md 文件，排除当前章节，
    使用 split_chapter_into_scenes() 切分后按倒序拼接。

    Args:
        current_chapter_path: 当前章节路径（排除）
        project_root: 项目根目录
        counter: Token 计数器
        budget: 可用 Token 预算

    Returns:
        拼接后的历史文本（倒序），超预算时截断
    """
    draft_dir = project_root / "draft"
    if not draft_dir.exists():
        return ""

    # 扫描所有章节文件，按文件名排序
    chapter_files = sorted(draft_dir.glob("*.md"))
    # 排除当前章节
    chapter_files = [f for f in chapter_files if f != current_chapter_path]

    if not chapter_files:
        return ""

    # 倒序读取（最近的章节优先）
    history_parts: list[str] = []
    total_tokens = 0

    for chapter_file in reversed(chapter_files):
        try:
            body = chapter_file.read_text(encoding="utf-8")
            # 跳过 Frontmatter，只取正文
            if body.startswith("---"):
                parts = body.split("---", 2)
                if len(parts) >= 3:
                    body = parts[2].strip()
        except Exception:
            continue

        if not body:
            continue

        # 切分场景
        scenes = split_chapter_into_scenes(body, max_tokens=2000)
        chapter_text = "\n\n".join(scenes)

        # 检查预算
        chapter_tokens = counter.count(chapter_text)
        if total_tokens + chapter_tokens > budget:
            # 截断到剩余预算
            remaining = budget - total_tokens
            if remaining > 0:
                chapter_text = counter.truncate_to_budget(chapter_text, remaining)
                history_parts.append(f"[{chapter_file.stem}]\n{chapter_text}")
            break

        history_parts.append(f"[{chapter_file.stem}]\n{chapter_text}")
        total_tokens += chapter_tokens

    return "\n\n".join(history_parts)


def _apply_circuit_breaker(
    messages: list[ContextMessage],
    counter: TokenCounter,
    budget: int,
) -> list[ContextMessage]:
    """应用 Token 熔断机制，超限时按权威层级从低到高截断。

    Args:
        messages: 待检查的消息列表
        counter: Token 计数器
        budget: 总 Token 预算

    Returns:
        裁剪后的消息列表
    """
    total = sum(m.token_count for m in messages)
    if total <= budget:
        return messages

    logger.warning("Token 超限 (%d > %d)，开始按权威层级裁剪", total, budget)

    # 按裁剪优先级（从低权威到高权威）逐层截断
    for authority_level in TRUNCATION_ORDER:
        if total <= budget:
            break
        for msg in messages:
            if msg.authority == authority_level and msg.token_count > 0:
                # 计算需要减少的 Token 数
                excess = total - budget
                new_token_count = max(0, msg.token_count - excess)
                if new_token_count == 0:
                    # 完全移除该消息
                    total -= msg.token_count
                    msg.token_count = 0
                    msg.content = ""
                else:
                    # 截断内容
                    msg.content = counter.truncate_to_budget(msg.content, new_token_count)
                    total -= msg.token_count - new_token_count
                    msg.token_count = new_token_count

    # 过滤掉空消息
    messages = [m for m in messages if m.token_count > 0 or m.authority is None]
    return messages
