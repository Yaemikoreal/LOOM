"""Agent 自治引擎 — Mid-Write 工具调用循环。

基于 ADR 0006 Agent Autonomy（Agent 自治）设计：
Writer 在创作过程中可主动挂起并调用 ToolRegistry 查询缺失设定，
而非仅在 think→write 之间做静态知识缺口检测。

使用方式:
    parser = ToolCallParser()
    request = parser.parse(agent_text)
    if request:
        result = executor.execute(request, safety_fence)
        # 注入结果，继续创作

核心协议：LLM 输出包含 ##TOOL_CALL## 标记时视为工具调用请求。

依赖: SafetyFence, ToolRegistry, LLMBus
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeResult, KnowledgeSource

logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────────────────


@dataclass
class AutonomousConfig:
    """Agent 自治配置参数。

    Attributes:
        max_tool_calls_per_write: 单次创作中最大工具调用次数
        max_tool_calls_total: 全局最大工具调用次数（跨所有 Agent 调用）
        enabled: 是否启用自治能力
    """

    max_tool_calls_per_write: int = 3
    max_tool_calls_total: int = 10
    enabled: bool = True


# ── 工具调用协议 ─────────────────────────────────────────────────────────

# 工具调用标记格式：##TOOL_CALL## tool_name|查询内容|查询原因
_TOOL_CALL_MARKER = "##TOOL_CALL##"
_TOOL_CALL_RE = re.compile(
    rf"{re.escape(_TOOL_CALL_MARKER)}\s*(\w+)\|(.+?)(?:\|(.+))?$",
    re.MULTILINE,
)

# Writer 的自治创作 Prompt 后缀（指导 LLM 在需要时发起工具调用）
_AUTONOMY_PROMPT_SUFFIX = """

### Agent 自治 — 知识查询协议

如果你在创作过程中发现缺少必要的世界观设定、角色状态或事件信息，
请使用以下格式发起查询：

##TOOL_CALL## 工具名|查询内容|查询原因

支持的工具有：
- **query_canon**: 查询世界观设定（如魔法规则、世界历史）
- **query_character**: 查询角色当前状态（伤势、情绪、位置）
- **query_event**: 查询历史事件记录
- **query_subconscious**: 查询潜意识灵感池

示例：
##TOOL_CALL## query_canon|魔法消耗寿命规则|需要确认魔法系统设定
##TOOL_CALL## query_character|char_001|查看角色当前伤势状态

每次查询后你会获得返回信息。请注意：
1. 只查询真正需要的信息，不要过度查询
2. 获得信息后直接继续创作，不需要对工具调用本身做解释
3. 将查询到的信息自然融入正文中

如果你不需要额外信息，直接输出正文即可。
"""


@dataclass
class ToolCallRequest:
    """解析自 LLM 输出的工具调用请求。

    Attributes:
        tool_name: 工具名称（query_canon / query_character / query_event / query_subconscious）
        query: 查询内容
        reason: 查询原因（可选）
        raw_text: 原始匹配文本
    """

    tool_name: str
    query: str
    reason: str = ""
    raw_text: str = ""

    @property
    def knowledge_source(self) -> KnowledgeSource | None:
        """将工具名映射到 KnowledgeSource。"""
        mapping = {
            "query_canon": KnowledgeSource.CANON,
            "query_subconscious": KnowledgeSource.SUBCONSCIOUS,
            "query_character": KnowledgeSource.CHARACTER,
            "query_event": KnowledgeSource.EVENT,
        }
        return mapping.get(self.tool_name)


# ── 工具调用结果格式 ─────────────────────────────────────────────────────

_TOOL_RESULT_TEMPLATE = """
##TOOL_RESULT## ({source})
查询: {query}
结果:
{content}

请将以上信息自然融入创作，然后继续输出正文。
"""


# ── 解析器 ───────────────────────────────────────────────────────────────


class ToolCallParser:
    """解析 LLM 输出中的工具调用标记。

    从 LLM 生成的文本中检测 ##TOOL_CALL## 标记，
    提取工具名、查询内容和原因。
    """

    @staticmethod
    def parse(text: str) -> ToolCallRequest | None:
        """从文本中解析工具调用请求。

        Args:
            text: LLM 输出的文本

        Returns:
            解析成功返回 ToolCallRequest，否则返回 None
        """
        if _TOOL_CALL_MARKER not in text:
            return None

        m = _TOOL_CALL_RE.search(text)
        if not m:
            return None

        tool_name = m.group(1).strip().lower()
        query = m.group(2).strip()
        reason = m.group(3).strip() if m.group(3) else ""

        # 验证工具名
        valid_tools = {"query_canon", "query_character", "query_event", "query_subconscious"}
        if tool_name not in valid_tools:
            logger.warning("未知工具: %s", tool_name)
            return None

        if not query:
            logger.warning("工具调用查询内容为空")
            return None
        if query.startswith("|"):
            logger.warning("工具调用查询内容为空（空字段）")
            return None

        return ToolCallRequest(
            tool_name=tool_name,
            query=query,
            reason=reason,
            raw_text=m.group(0),
        )

    @staticmethod
    def format_result(
        request: ToolCallRequest,
        content: str,
        source_label: str = "unknown",
    ) -> str:
        """格式化工具调用结果为 LLM 可读的消息。

        Args:
            request: 原始工具调用请求
            content: 查询返回的内容
            source_label: 来源标签

        Returns:
            格式化的结果字符串
        """
        truncated = content[:1500] if content else "无相关结果"
        return _TOOL_RESULT_TEMPLATE.format(
            source=source_label,
            query=request.query,
            content=truncated,
        ).strip()

    @staticmethod
    def get_autonomy_prompt_suffix() -> str:
        """获取 Writer 的自治 Prompt 后缀。

        Returns:
            指导 LLM 使用工具调用的提示文本
        """
        return _AUTONOMY_PROMPT_SUFFIX


# ── 执行器 ───────────────────────────────────────────────────────────────


class ToolCallExecutor:
    """工具调用执行器 — 将 ToolCallRequest 路由到 ToolRegistry。"""

    def __init__(self, tool_registry: Any) -> None:
        self._tool_registry = tool_registry

    def execute(self, request: ToolCallRequest) -> KnowledgeResult:
        """执行单个工具调用请求。

        Args:
            request: 工具调用请求

        Returns:
            知识查询结果
        """
        source = request.knowledge_source
        if source is None:
            return KnowledgeResult(
                content=f"未知工具: {request.tool_name}",
                source=KnowledgeSource.CANON,
                concept=request.query,
                relevance=0.0,
            )

        need = KnowledgeNeed(
            concept=request.query,
            source=source,
            context=request.reason,
            character_id=request.query if "char_" in request.query else "",
        )

        results = self._tool_registry.fulfill([need])
        if results:
            return results[0]

        return KnowledgeResult(
            content="",
            source=source,
            concept=request.query,
            relevance=0.0,
        )

    def format_for_llm(self, result: KnowledgeResult) -> str:
        """将执行结果格式化为 LLM 可读字符串。

        Args:
            result: 查询结果

        Returns:
            格式化文本
        """
        return ToolCallParser.format_result(
            ToolCallRequest(
                tool_name=result.source.value,
                query=result.concept,
            ),
            content=result.content,
            source_label=result.source.value,
        )


# ── 自治写循环 ───────────────────────────────────────────────────────────


class AutonomousWriteLoop:
    """自治创作循环 — 带工具调用能力的多轮写作。

    循环过程：
    1. 发送创作 Prompt（含工具调用协议说明）
    2. 解析 LLM 输出中的工具调用标记
    3. 有工具调用时：执行→注入结果→继续循环
    4. 无工具调用时：返回最终正文
    5. 超限时：返回当前已生成内容
    """

    def __init__(
        self,
        llm_bus: Any,
        executor: ToolCallExecutor,
        safety_fence: Any,
        config: AutonomousConfig | None = None,
    ) -> None:
        self.llm_bus = llm_bus
        self.executor = executor
        self.safety_fence = safety_fence
        self.config = config or AutonomousConfig()

    def execute(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        agent_name: str = "writer",
    ) -> str:
        """执行自治创作循环。

        Args:
            messages: 初始消息列表（已包含上下文和创作任务）
            model: LLM 模型名称
            agent_name: Agent 名称（用于安全围栏日志）

        Returns:
            最终创作的正文

        Raises:
            RuntimeError: 安全围栏违规时
        """
        if not self.config.enabled:
            return self._single_call(messages, model)

        # 使用 autonomous_call 上下文管理器
        with self.safety_fence.autonomous_call(agent_name):
            return self._run_loop(messages, model, agent_name)

    def _single_call(self, messages: list[dict], model: str | None) -> str:
        """单次 LLM 调用（无自治能力）。"""
        response = self.llm_bus.chat(messages, temperature=0.8, max_tokens=4000, model=model)
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("LLM 返回空文本")
        return text.strip()

    def _run_loop(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        agent_name: str,
    ) -> str:
        """自治循环核心。

        Args:
            messages: 消息列表（会在循环中追加）
            model: 模型名称
            agent_name: Agent 名称

        Returns:
            最终正文

        Raises:
            RuntimeError: 安全围栏违规时
        """
        tool_call_count = 0
        parser = ToolCallParser()
        accumulated_text = ""

        for _iteration in range(self.config.max_tool_calls_per_write + 1):
            # 安全检查
            if not self.safety_fence.check_all(agent_name):
                violation = (
                    self.safety_fence.violations[-1] if self.safety_fence.violations else None
                )
                detail = violation.detail if violation else "未知违规"
                logger.warning("自治循环被安全围栏中断: %s", detail)
                if accumulated_text:
                    return accumulated_text.strip()
                raise RuntimeError(f"安全围栏阻止自治创作: {detail}")

            # 调用 LLM
            response = self.llm_bus.chat(
                messages,
                temperature=0.8,
                max_tokens=4000,
                model=model,
            )
            text = response.choices[0].message.content
            if not text:
                if accumulated_text:
                    return accumulated_text.strip()
                raise RuntimeError("自治创作 LLM 返回空文本")

            # 记录 Token 消耗
            if hasattr(response, "usage") and response.usage:
                tokens = (response.usage.prompt_tokens or 0) + (
                    response.usage.completion_tokens or 0
                )
                self.safety_fence.record_tokens(tokens)

            # 检查是否有工具调用
            request = parser.parse(text)
            if request is None:
                # 无工具调用 → 正常完成
                return text.strip()

            # 有工具调用 → 执行并继续
            tool_call_count += 1  # noqa: SIM113
            if tool_call_count > self.config.max_tool_calls_total:
                logger.warning(
                    "全局工具调用次数超限 (%d > %d)，返回当前内容",
                    tool_call_count,
                    self.config.max_tool_calls_total,
                )
                # 从文本中移除工具调用标记，保留正文部分
                clean_text = self._remove_tool_call(text, request)
                if clean_text:
                    return clean_text.strip()
                if accumulated_text:
                    return accumulated_text.strip()
                raise RuntimeError("工具调用超限且无法提取正文")

            # 执行工具调用
            try:
                result = self.executor.execute(request)
                result_text = self.executor.format_for_llm(result)
                logger.info(
                    "自治工具调用 %d/%d: %s | %s",
                    tool_call_count,
                    self.config.max_tool_calls_per_write,
                    request.tool_name,
                    request.query[:50],
                )
            except Exception as e:
                logger.warning("工具调用执行失败: %s", e)
                result_text = f"##TOOL_RESULT## (error)\n查询失败: {e}"

            # 保存当前已生成的正文（移除工具调用标记）
            clean_part = self._remove_tool_call(text, request)
            if clean_part:
                accumulated_text = clean_part.strip()

            # 注入结果到消息列表
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": result_text})

        # 超出最大迭代次数
        logger.warning(
            "自治循环达到最大迭代次数 %d，返回当前内容",
            self.config.max_tool_calls_per_write,
        )
        if accumulated_text:
            return accumulated_text.strip()
        raise RuntimeError(f"自治创作超限（{self.config.max_tool_calls_per_write} 次工具调用）")

    @staticmethod
    def _remove_tool_call(text: str, request: ToolCallRequest) -> str:
        """从文本中移除工具调用标记行。

        Args:
            text: LLM 输出文本
            request: 已解析的工具调用请求

        Returns:
            移除标记后的文本
        """
        if request.raw_text in text:
            return text.replace(request.raw_text, "").strip()
        return text.strip()
