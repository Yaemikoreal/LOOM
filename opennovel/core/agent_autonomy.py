"""Agent 自治引擎 — Mid-Write 工具调用循环。

基于 ADR 0010 Agent Autonomy（Agent 自治）设计：
Writer 在创作过程中可主动挂起并调用 ToolRegistry 查询缺失设定，
而非仅在 think→write 之间做静态知识缺口检测。

核心协议（Phase 3 升级版 — 双轨架构）：
- 原生通道：走 LiteLLM 原生 Tool-Use（supports_native_tool_use=True）
- 回退通道：走 <tool_call>JSON</tool_call> XML/JSON 混合块解析

使用方式:
    parser = ToolCallParser()
    request = parser.parse(agent_text)
    if request:
        result = executor.execute(request, safety_fence, agent_name)
        # 注入结果，继续创作

依赖: SafetyFence, ToolRegistry, LLMBus
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from opennovel.schemas.knowledge import KnowledgeNeed, KnowledgeResult, KnowledgeSource

logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────────────────


@dataclass
class AutonomousConfig:
    """Agent 自治配置参数。

    Attributes:
        max_tool_calls_per_write: 单次创作中最大工具调用次数
        max_tool_calls_total: 全局最大工具调用次数（跨所有 Agent 调用，实例级累加）
        max_tool_call_history: 消息列表中保留的最大工具调用历史轮次
        enabled: 是否启用自治能力
        supports_native_tool_use: 是否启用原生 Tool-Use 通道（需模型支持）
    """

    max_tool_calls_per_write: int = 3
    max_tool_calls_total: int = 10
    max_tool_call_history: int = 5
    enabled: bool = True
    supports_native_tool_use: bool = False


# ── 工具调用协议（回退通道） ─────────────────────────────────────────────

# XML/JSON 混合块格式（完全替代旧管道符 ##TOOL_CALL##）
# 格式: <tool_call>{"tool": "...", "args": {...}, "reason": "..."}</tool_call>
_TOOL_CALL_XML_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)

_VALID_TOOLS = {"query_canon", "query_character", "query_event", "query_subconscious"}

# Writer 的自治创作 Prompt 后缀（指导 LLM 在需要时发起工具调用）
_AUTONOMY_PROMPT_SUFFIX = """

### Agent 自治 — 知识查询协议

如果你在创作过程中发现缺少必要的世界观设定、角色状态或事件信息，
请使用以下格式发起查询：

<tool_call>
{"tool": "query_canon", "args": {"query": "查询内容"}, "reason": "查询原因"}
</tool_call>

支持的工具有：
- **query_canon**: 查询世界观设定（如魔法规则、世界历史）
- **query_character**: 查询角色当前状态（伤势、情绪、位置）
- **query_event**: 查询历史事件记录
- **query_subconscious**: 查询潜意识灵感池

示例：
<tool_call>
{"tool": "query_canon", "args": {"query": "魔法消耗寿命规则"}, "reason": "需要确认魔法系统设定"}
</tool_call>
<tool_call>
{"tool": "query_character", "args": {"query": "char_001"}, "reason": "查看角色当前伤势状态"}
</tool_call>

每次查询后你会获得返回信息。请注意：
1. 只查询真正需要的信息，不要过度查询
2. 获得信息后直接继续创作，不需要对工具调用本身做解释
3. 将查询到的信息自然融入正文中

如果你不需要额外信息，直接输出正文即可。
"""

# 原生 Tool-Use 通道的 Prompt 后缀（更简洁，因为协议由 LLM API 层处理）
_NATIVE_TOOL_PROMPT_SUFFIX = """

### Agent 自治提示

如果你在创作过程中发现缺少必要的世界观设定、角色状态或事件信息，
请使用提供的工具函数进行查询。

获得信息后直接继续创作，不需要对工具调用本身做解释。
"""


@dataclass
class ToolCallRequest:
    """解析自 LLM 输出的工具调用请求。

    Attributes:
        tool_name: 工具名称（query_canon / query_character / query_event / query_subconscious）
        query: 查询内容
        args: 参数字典
        reason: 查询原因（可选）
        raw_text: 原始匹配文本
    """

    tool_name: str
    query: str
    args: dict = field(default_factory=dict)
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

    回退通道：从 LLM 生成的文本中检测 <tool_call> XML/JSON 块，
    提取工具名、参数和原因。

    原生通道：由 LiteLLM 的 tool_calls 响应处理，不走此解析器。
    """

    @staticmethod
    def parse(text: str) -> ToolCallRequest | None:
        """从文本中解析工具调用请求（回退通道）。

        Args:
            text: LLM 输出的文本

        Returns:
            解析成功返回 ToolCallRequest，否则返回 None
        """
        if "<tool_call>" not in text:
            return None

        m = _TOOL_CALL_XML_RE.search(text)
        if not m:
            return None

        # 解析 JSON
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            logger.warning("工具调用 JSON 解析失败: %s", e)
            return None

        tool_name = str(payload.get("tool", "")).strip().lower()
        args = payload.get("args", {})
        reason = str(payload.get("reason", "")).strip()
        raw_text = m.group(0)

        # 提取 query（从 args 中取，兼容 string 和 dict 两种格式）
        query = ""
        query = str(args.get("query", "")).strip() if isinstance(args, dict) else str(args).strip()

        # 验证工具名
        if tool_name not in _VALID_TOOLS:
            logger.warning("未知工具: %s", tool_name)
            return None

        if not query:
            logger.warning("工具调用查询内容为空")
            return None

        return ToolCallRequest(
            tool_name=tool_name,
            query=query,
            args=args if isinstance(args, dict) else {},
            reason=reason,
            raw_text=raw_text,
        )

    @staticmethod
    def parse_native(
        tool_name: str,
        arguments: dict,
    ) -> ToolCallRequest | None:
        """从原生 Tool-Use 响应创建 ToolCallRequest。

        Args:
            tool_name: 工具名称
            arguments: 工具参数字典

        Returns:
            ToolCallRequest 或 None（工具名不合法时）
        """
        if tool_name not in _VALID_TOOLS:
            logger.warning("原生工具调用: 未知工具 %s", tool_name)
            return None

        query = str(arguments.get("query", "")).strip()
        if not query:
            logger.warning("原生工具调用: 查询内容为空")
            return None

        return ToolCallRequest(
            tool_name=tool_name,
            query=query,
            args=arguments,
            reason=str(arguments.get("reason", "")).strip(),
            raw_text="",
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
    def get_autonomy_prompt_suffix(native: bool = False) -> str:
        """获取 Writer 的自治 Prompt 后缀。

        Args:
            native: 是否使用原生 Tool-Use 通道

        Returns:
            指导 LLM 使用工具调用的提示文本
        """
        return _NATIVE_TOOL_PROMPT_SUFFIX if native else _AUTONOMY_PROMPT_SUFFIX


# ── 执行器 ───────────────────────────────────────────────────────────────


class ToolCallExecutor:
    """工具调用执行器 — 将 ToolCallRequest 路由到 ToolRegistry。"""

    def __init__(
        self,
        tool_registry: Any,
        safety_fence: Any | None = None,
        agent_name: str = "",
    ) -> None:
        self._tool_registry = tool_registry
        self._safety_fence = safety_fence
        self._agent_name = agent_name

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
            character_id=(request.query if re.match(r"^char_\d+$", request.query) else ""),
        )

        # 调用 execute()（带权限检查和重试），而非直接 fulfill()
        result = self._tool_registry.execute(
            need,
            safety_fence=self._safety_fence,
            agent=self._agent_name,
        )
        return result

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

    支持双轨路由：
    - 原生通道：走 LiteLLM tools 参数 + tool_calls 响应
    - 回退通道：走 <tool_call> XML/JSON 解析
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
        # 全局工具调用计数（跨多次 execute() 调用累加）
        self._global_tool_call_count = 0
        # 工具调用历史（滑动窗口）
        self._tool_call_history: list[tuple[str, str]] = []

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
            if self.config.supports_native_tool_use:
                return self._run_native_loop(messages, model, agent_name)
            return self._run_fallback_loop(messages, model, agent_name)

    def _single_call(self, messages: list[dict], model: str | None) -> str:
        """单次 LLM 调用（无自治能力）。"""
        response = self.llm_bus.chat(messages, temperature=0.8, max_tokens=4000, model=model)
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("LLM 返回空文本")
        return text.strip()

    def _inject_tool_call_history(self, messages: list[dict]) -> None:
        """将工具调用历史注入消息列表（滑动窗口，保留最近 N 条）。

        Args:
            messages: 当前消息列表
        """
        if not self._tool_call_history:
            return

        # 只保留最近的 max_tool_call_history 条
        recent = self._tool_call_history[-self.config.max_tool_call_history :]

        history_lines = ["## 本次创作已使用的工具调用历史"]
        for tool_name, query in recent:
            history_lines.append(f"- {tool_name}: {query[:80]}")

        messages.append({"role": "system", "content": "\n".join(history_lines)})

    def _run_fallback_loop(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        agent_name: str,
    ) -> str:
        """回退通道自治循环（<tool_call> XML/JSON 解析）。

        Args:
            messages: 消息列表（会在循环中追加）
            model: 模型名称
            agent_name: Agent 名称

        Returns:
            最终正文
        """
        parser = ToolCallParser()
        accumulated_text = ""

        # 注入工具调用历史
        self._inject_tool_call_history(messages)

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
            self._global_tool_call_count += 1
            if self._global_tool_call_count > self.config.max_tool_calls_total:
                logger.warning(
                    "全局工具调用次数超限 (%d > %d)，返回当前内容",
                    self._global_tool_call_count,
                    self.config.max_tool_calls_total,
                )
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
                    self._global_tool_call_count,
                    self.config.max_tool_calls_per_write,
                    request.tool_name,
                    request.query[:50],
                )
                # 记录到历史
                self._tool_call_history.append((request.tool_name, request.query))
            except Exception as e:
                logger.warning("工具调用执行失败: %s", e)
                result_text = f"##TOOL_RESULT## (error)\n查询失败: {e}"

            # 保存当前已生成的正文（移除工具调用标记）
            clean_part = self._remove_tool_call(text, request)
            if clean_part:
                accumulated_text = clean_part.strip()

            # 注入结果到消息列表（使用写死在 python 中的历史注入）
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

    def _run_native_loop(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        agent_name: str,
    ) -> str:
        """原生通道自治循环（LiteLLM Tool-Use）。

        使用 LLM 的原生 tool_calls 机制，不走文本标记解析。

        Args:
            messages: 消息列表
            model: 模型名称
            agent_name: Agent 名称

        Returns:
            最终正文
        """
        accumulated_text = ""
        parser = ToolCallParser()

        # 注入工具调用历史
        self._inject_tool_call_history(messages)

        # 定义 tools 声明
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "query_canon",
                    "description": "查询世界观设定文档",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "查询内容，如'魔法消耗寿命规则'",
                            },
                            "reason": {
                                "type": "string",
                                "description": "查询原因",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_character",
                    "description": "查询角色当前状态",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "角色 ID，如 char_001",
                            },
                            "reason": {
                                "type": "string",
                                "description": "查询原因",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_event",
                    "description": "查询历史事件记录",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "查询内容",
                            },
                            "reason": {
                                "type": "string",
                                "description": "查询原因",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_subconscious",
                    "description": "查询潜意识灵感池",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "查询内容",
                            },
                            "reason": {
                                "type": "string",
                                "description": "查询原因",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

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

            # 调用 LLM（带 tools 声明）
            response = self.llm_bus.chat(
                messages,
                temperature=0.8,
                max_tokens=4000,
                model=model,
                tools=tools,
                tool_choice="auto",
            )
            choice = response.choices[0]
            text = choice.message.content or ""
            tool_calls = getattr(choice.message, "tool_calls", None)

            # 记录 Token 消耗
            if hasattr(response, "usage") and response.usage:
                tokens = (response.usage.prompt_tokens or 0) + (
                    response.usage.completion_tokens or 0
                )
                self.safety_fence.record_tokens(tokens)

            # 无工具调用 → 正常完成
            if not tool_calls:
                return text.strip() if text else (accumulated_text.strip() or "")

            # 有工具调用 → 执行每一个
            messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})

            for tc in tool_calls:
                tc_id = tc.id
                tc_name = tc.function.name
                try:
                    tc_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tc_args = {}

                # 解析原生工具调用
                request = parser.parse_native(tc_name, tc_args)
                if request is None:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": f"未知工具或参数无效: {tc_name}",
                        }
                    )
                    continue

                # 全局调用计数
                self._global_tool_call_count += 1
                if self._global_tool_call_count > self.config.max_tool_calls_total:
                    logger.warning(
                        "全局工具调用次数超限 (%d > %d)，返回当前内容",
                        self._global_tool_call_count,
                        self.config.max_tool_calls_total,
                    )
                    if accumulated_text:
                        return accumulated_text.strip()
                    raise RuntimeError(f"工具调用超限（{self.config.max_tool_calls_total}）")

                # 执行工具
                try:
                    result = self.executor.execute(request)
                    result_content = result.content or ""
                    logger.info(
                        "原生工具调用 %d/%d: %s | %s",
                        self._global_tool_call_count,
                        self.config.max_tool_calls_per_write,
                        request.tool_name,
                        request.query[:50],
                    )
                    self._tool_call_history.append((request.tool_name, request.query))
                except Exception as e:
                    logger.warning("原生工具调用执行失败: %s", e)
                    result_content = f"[检索失败: {e}]"

                # 注入 tool_result
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result_content,
                    }
                )

            # 保存正文（如果有）
            if text.strip():
                accumulated_text = text.strip()

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
        if request.raw_text and request.raw_text in text:
            return text.replace(request.raw_text, "").strip()
        return text.strip()


# ── ADR 0010 兼容别名 ──
AgentAutonomy = AutonomousWriteLoop
