"""IntentParser — 用户意图分类器。

LLM 分类 + 关键词 fallback 双层策略。
支持 / 命令前缀的显式路由。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("opennovel.desktop.intent_parser")


@dataclass
class IntentResult:
    """意图解析结果。"""

    action_type: str  # "write" | "revise" | "evaluate" | "stash" | "commit" | "search" | "chat"
    confidence: float  # 0.0–1.0
    params: dict[str, str]  # 提取的参数如 {"text": "..."}
    requires_confirmation: bool = False
    raw_response: str = ""


# 关键词 fallback 模式（与原 _detect_chat_intent 兼容）
_KEYWORD_PATTERNS: dict[str, list[str]] = {
    "write": ["写", "创作", "续写", "写一段", "写一个", "写一章", "写一篇"],
    "evaluate": ["评价", "评分", "评估", "批评", "打分", "检查一下", "看看"],
    "revise": ["修改", "润色", "重写", "改一下", "优化", "改得", "调整"],
    "stash": ["存", "灵感", "保存这个", "记一下", "记下来"],
    "commit": ["提交", "commit", "固化"],
    "search": ["搜索", "查找", "找一下", "找找"],
}

# Few-shot prompt 模板
_INTENT_PROMPT = """你是一个小说写作 AI 助手的意图分类器。
分析用户的请求，将其归类为以下之一并输出 JSON：

类型: write(创作), revise(修改/润色), evaluate(评价), stash(存灵感),
  commit(提交), search(搜索), chat(闲聊/其他)

输出: {"action_type":"<类型>","confidence":0.0-1.0,"params":{"text":"<内容>"}}

示例：
- "写一章古城的情节" → {"action_type":"write","confidence":0.95,
    "params":{"text":"主角进入古城"}}
- "把这段改得更阴暗些" → {"action_type":"revise","confidence":0.9,
    "params":{"text":"改得更阴暗"}}
- "检查角色一致性" → {"action_type":"evaluate","confidence":0.85,
    "params":{"text":"检查角色一致性"}}
- "今天天气不错" → {"action_type":"chat","confidence":0.5,
    "params":{"text":"今天天气不错"}}

现在分类："""


class IntentParser:
    """双层意图分类器。

    策略：
    1. 以 / 开头 → 直接命令路由（不调 LLM）
    2. 有 LLM 配置 → 调 LLM 分类
    3. LLM 不可用/超时 → 关键词 fallback
    """

    def __init__(self, config: dict[str, str] | None = None) -> None:
        self._config = config or {}
        self._llm_available: bool = bool(self._config.get("model"))

    # ── 公共入口 ──────────────────────────────────────────

    def parse(self, text: str, context: dict[str, Any] | None = None) -> IntentResult:
        """解析用户输入意图。

        Args:
            text: 用户自然语言输入
            context: 可选上下文 {current_file, current_project, editor_mode}

        Returns:
            IntentResult（永远不会返回 None，最差返回 "chat" 类型）
        """
        context = context or {}

        # 1. / 命令显式路由
        if text.startswith("/"):
            return self._handle_slash(text)

        # 2. 尝试 LLM
        if self._llm_available:
            try:
                result = self._parse_with_llm(text, context)
                if result is not None:
                    return result
            except Exception:
                logger.debug("LLM intent parse failed, falling back to keywords")

        # 3. 关键词 fallback
        return self._parse_with_keywords(text)

    # ── Slash 命令 ────────────────────────────────────────

    @staticmethod
    def _handle_slash(text: str) -> IntentResult:
        """处理 /command 语法。"""
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0].lower().strip()
        args = parts[1] if len(parts) > 1 else ""

        command_map: dict[str, str] = {
            "write": "write",
            "auto": "write",
            "w": "write",
            "写": "write",
            "revise": "revise",
            "修改": "revise",
            "润色": "revise",
            "r": "revise",
            "evaluate": "evaluate",
            "评估": "evaluate",
            "评价": "evaluate",
            "e": "evaluate",
            "stash": "stash",
            "灵感": "stash",
            "存": "stash",
            "commit": "commit",
            "提交": "commit",
            "search": "search",
            "搜索": "search",
            "find": "search",
            "help": "help",
            "帮助": "help",
            "h": "help",
        }

        action = command_map.get(cmd, "unknown")
        if action == "unknown":
            return IntentResult(
                action_type="chat",
                confidence=0.8,
                params={"text": text},
                raw_response=f"未知命令: /{cmd}",
            )

        if action == "help":
            help_text = (
                "可用: /write 创作 /revise 修订 /evaluate 评价 "
                "/stash 灵感 /commit 提交 /search 搜索"
            )
            return IntentResult(
                action_type="chat",
                confidence=1.0,
                params={"text": help_text},
            )

        return IntentResult(
            action_type=action,
            confidence=1.0,
            params={"text": args},
        )

    # ── LLM 分类 ──────────────────────────────────────────

    def _parse_with_llm(self, text: str, context: dict[str, Any]) -> IntentResult | None:
        """使用 LLM 进行意图分类。"""
        try:
            import litellm  # noqa: PLC0415

            messages = [
                {"role": "system", "content": _INTENT_PROMPT},
                {"role": "user", "content": text},
            ]

            model = self._config.get("model", "deepseek/deepseek-v4-flash")
            api_key = self._config.get("api_key") or None
            api_base = self._config.get("api_base") or None

            response = litellm.completion(
                model=model,
                api_key=api_key,
                api_base=api_base,
                messages=messages,
                max_tokens=200,
                timeout=8,
                temperature=0,
            )

            content = ""
            if hasattr(response, "choices") and response.choices:
                content = response.choices[0].message.content or ""
            elif isinstance(response, dict):
                choices = response.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")

            if content:
                return self._parse_json_response(content, text)

        except Exception:
            raise  # 上层 catch 后走关键词 fallback

        return None

    @staticmethod
    def _parse_json_response(content: str, original_text: str) -> IntentResult | None:
        """解析 LLM 返回的 JSON。"""
        # 提取 JSON 块
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        try:
            data = json.loads(content)
            return IntentResult(
                action_type=str(data.get("action_type", "chat")),
                confidence=float(data.get("confidence", 0.6)),
                params=data.get("params", {}),
                raw_response=content,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            # JSON 解析失败，尝试从文本中提取 action_type
            for action in ("write", "revise", "evaluate", "stash", "commit", "search"):
                if action in content.lower():
                    return IntentResult(
                        action_type=action,
                        confidence=0.4,
                        params={"text": original_text},
                        raw_response=content,
                    )
            return None

    # ── 关键词 fallback ───────────────────────────────────

    @staticmethod
    def _parse_with_keywords(text: str) -> IntentResult:
        """纯关键词匹配（与原 _detect_chat_intent 行为一致）。"""
        t = text.lower()
        best_action = "chat"
        best_priority = 999

        # 按优先级排序（越具体的关键词优先级越高）
        priority_order = ["commit", "stash", "search", "revise", "evaluate", "write"]
        for action in priority_order:
            keywords = _KEYWORD_PATTERNS.get(action, [])
            if any(w in t for w in keywords):
                idx = min(
                    (t.find(w) for w in keywords if w in t),
                    default=999,
                )
                if idx < best_priority:
                    best_priority = idx
                    best_action = action

        return IntentResult(
            action_type=best_action,
            confidence=0.5 if best_action != "chat" else 0.3,
            params={"text": text},
            raw_response=f"keyword:{best_action}",
        )
