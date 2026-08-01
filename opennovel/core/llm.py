"""LiteLLM 封装与重试机制 - 统一模型调用总线。

基于 LiteLLM + tenacity 实现：
- 模型无关的统一调用接口（屏蔽 OpenAI/Anthropic/本地模型差异）
- 自动重试与限流容错
- 流式输出支持
- Token 用量追踪
- Prompt 日志记录（可选）
"""

import json
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

from litellm import acompletion, completion
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# Glass-Box Decision: 跨模块隐式关联的追踪标识
# AutoRunner.run_chapter() 入口设置，_log_prompt 中读取
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

# LiteLLM 可重试的异常类型（仅网络/限流/超时类异常）
RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
)


class LLMBus:
    """LLM 统一调用总线，封装 LiteLLM 并提供重试与限流能力。

    使用方式:
        bus = LLMBus(model="gpt-4", default_max_tokens=2000)
        response = bus.chat(messages=[...])
    """

    def __init__(
        self,
        model: str = "gpt-4",
        default_max_tokens: int = 2000,
        default_temperature: float = 0.7,
        max_retries: int = 3,
        api_base: str | None = None,
        api_key: str | None = None,
        metrics_store: Any = None,
        agent_name: str = "",
        prompt_log_dir: Path | None = None,
    ) -> None:
        """初始化 LLM 总线。

        Args:
            model: 默认模型名称，支持 LiteLLM 的所有模型标识
            default_max_tokens: 默认最大输出 Token 数
            default_temperature: 默认生成温度
            max_retries: 最大重试次数
            api_base: 自定义 API 端点（用于 OpenAI 兼容接口）
            api_key: API 密钥（优先级高于环境变量）
            metrics_store: 指标数据库实例（可选，用于自动记录 token 消耗）
            agent_name: Agent 名称（配合 metrics_store 使用）
            prompt_log_dir: Prompt 日志目录（可选，记录每次 LLM 调用的完整 Prompt）
        """
        self.model = model
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature
        self.max_retries = max_retries
        self.api_base = api_base
        self.api_key = api_key
        self.metrics_store = metrics_store
        self.agent_name = agent_name
        self.prompt_log_dir = prompt_log_dir

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """同步调用 LLM 进行对话。

        Args:
            messages: 消息列表，格式为 [{"role": "system/user/assistant", "content": "..."}]
            model: 覆盖默认模型
            max_tokens: 覆盖默认最大输出 Token
            temperature: 覆盖默认温度
            **kwargs: 传递给 LiteLLM 的额外参数

        Returns:
            LiteLLM 的原始响应字典
        """
        call_kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.default_max_tokens,
            "temperature": temperature if temperature is not None else self.default_temperature,
        }
        if self.api_base:
            call_kwargs["api_base"] = self.api_base
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        call_kwargs.update(kwargs)

        response = completion(**call_kwargs)
        logger.debug(
            "LLM 调用完成: model=%s, usage=%s",
            model or self.model,
            getattr(response, "usage", None),
        )
        # 记录 Prompt 日志
        response_text = ""
        try:
            response_text = response.choices[0].message.content or ""
        except (AttributeError, IndexError):
            pass
        self._log_prompt(messages, model or self.model, response_text)

        self._record_usage(response, model or self.model, kwargs.get("chapter_id", ""))
        return response

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def achat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """异步调用 LLM 进行对话。

        Args:
            messages: 消息列表
            model: 覆盖默认模型
            max_tokens: 覆盖默认最大输出 Token
            temperature: 覆盖默认温度
            **kwargs: 传递给 LiteLLM 的额外参数

        Returns:
            LiteLLM 的原始响应字典
        """
        call_kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.default_max_tokens,
            "temperature": temperature if temperature is not None else self.default_temperature,
        }
        if self.api_base:
            call_kwargs["api_base"] = self.api_base
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        call_kwargs.update(kwargs)

        response = await acompletion(**call_kwargs)
        self._record_usage(response, model or self.model, kwargs.get("chapter_id", ""))
        return response

    async def achat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """异步流式调用 LLM，逐 Token 返回生成内容。

        适用于 loom write 的交互式续写场景。

        Args:
            messages: 消息列表
            model: 覆盖默认模型
            max_tokens: 覆盖默认最大输出 Token
            temperature: 覆盖默认温度
            **kwargs: 传递给 LiteLLM 的额外参数

        Yields:
            逐个生成的文本片段
        """
        call_kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.default_max_tokens,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "stream": True,
        }
        if self.api_base:
            call_kwargs["api_base"] = self.api_base
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        call_kwargs.update(kwargs)

        response = await acompletion(**call_kwargs)
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    def _log_prompt(
        self,
        messages: list[dict[str, str]],
        model: str,
        response_text: str = "",
    ) -> None:
        """记录完整 Prompt 到日志文件。

        Args:
            messages: 发送给 LLM 的消息列表
            model: 使用的模型名称
            response_text: LLM 返回的文本（可选）
        """
        if not self.prompt_log_dir:
            return
        try:
            self.prompt_log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"{timestamp}_{self.agent_name or 'unknown'}.json"
            log_path = self.prompt_log_dir / filename

            log_data = {
                "timestamp": datetime.now().isoformat(),
                "agent": self.agent_name,
                "model": model,
                "messages": messages,
                "response": response_text,
            }

            log_path.write_text(
                json.dumps(log_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # Glass-Box Decision: 从 JSON 响应中提取 reasoning 并独立存储
            if response_text:
                self._extract_and_save_reasoning(response_text)
        except Exception as e:
            logger.debug("Prompt 日志写入失败: %s", e)

    def _extract_and_save_reasoning(self, response_text: str) -> None:
        """从 LLM 响应中提取 reasoning 字段并存入独立文件。

        仅对 JSON 格式的响应（Think/Evaluate 阶段）有效。
        Write 阶段的纯文本响应直接跳过。
        文件保存到 prompt_log_dir 的上级目录下的 reasoning/ 子目录。
        """
        trace_id = trace_id_var.get()
        if not trace_id or not self.prompt_log_dir:
            return

        reasoning = None
        try:
            data = json.loads(response_text)
            if isinstance(data, dict):
                reasoning = data.get("reasoning") or data.get("critique_reasoning")
        except (json.JSONDecodeError, TypeError):
            return  # 非 JSON 响应（如 Write 阶段），跳过

        if not reasoning:
            return

        try:
            reasoning_dir = self.prompt_log_dir.parent / "reasoning"
            reasoning_dir.mkdir(parents=True, exist_ok=True)
            path = reasoning_dir / f"{trace_id}_{self.agent_name or 'unknown'}.json"
            log_data = {
                "trace_id": trace_id,
                "agent": self.agent_name,
                "timestamp": datetime.now().isoformat(),
                "reasoning": reasoning,
            }
            path.write_text(
                json.dumps(log_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("推理链日志写入失败: %s", e)

    def _record_usage(
        self, response: Any, model: str, chapter_id: str = ""
    ) -> None:
        """记录 token 使用量到指标数据库。"""
        if not self.metrics_store or not self.agent_name:
            return
        try:
            usage = getattr(response, "usage", None)
            if usage:
                self.metrics_store.record_token_usage(
                    agent=self.agent_name,
                    chapter_id=chapter_id,
                    model=model,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                )
        except Exception as e:
            logger.debug("记录 token 使用量失败: %s", e)


def extract_text_from_response(response: dict[str, Any]) -> str:
    """从 LLM 响应中提取生成的文本内容。

    Args:
        response: LiteLLM 的原始响应

    Returns:
        生成的文本内容
    """
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError, KeyError) as e:
        logger.error("无法从 LLM 响应中提取文本: %s", e)
        return ""


def extract_usage_from_response(response: dict[str, Any]) -> dict[str, int]:
    """从 LLM 响应中提取 Token 使用量。

    Args:
        response: LiteLLM 的原始响应

    Returns:
        包含 prompt_tokens, completion_tokens, total_tokens 的字典
    """
    try:
        usage = response.usage
        return {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
    except (AttributeError, KeyError) as e:
        logger.warning("无法从 LLM 响应中提取 Token 使用量: %s", e)
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
