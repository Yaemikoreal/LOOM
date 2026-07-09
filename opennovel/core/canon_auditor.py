"""LLM Canon Auditor — 基于 LLM 的世界观规则二次审计层。

在 CanonChecker（纯 Python 关键词匹配，阻断级）基础上，增加 LLM 二次审计，
用于检测复杂语义、隐喻、例外场景的 Canon 违反。LLM Auditor 只标记不阻断，
输出 canon_risk_score（0-1）和 findings 列表。

使用方式:
    auditor = LLMCanonAuditor(llm_bus=llm_bus, rules=rules)
    result = auditor.audit_text(chapter_text, chapter_id="ch_001")
    print(result.canon_risk_score, result.findings)

当 LLMBus 不可用时，返回空结果（risk_score=0.0，findings=[]），不报错。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opennovel.core.canon_checker import CanonRule
    from opennovel.core.llm import LLMBus

logger = logging.getLogger(__name__)

# ── 结果模型 ─────────────────────────────────────────────────────────────


@dataclass
class LLMCanonFinding:
    """LLM 发现的潜在 Canon 风险项。

    Attributes:
        rule_concept: 相关的世界观概念（如"船上武器"）
        detail: 风险详情说明
        severity: 严重程度（high / medium / low）
        snippet: 触发风险的文本片段（最多 100 字）
    """

    rule_concept: str
    detail: str
    severity: str = "medium"  # high / medium / low
    snippet: str = ""


@dataclass
class CanonAuditResult:
    """LLM Canon 审计结果。

    Attributes:
        canon_risk_score: 0-1 之间的风险分数，0 表示无风险
        findings: 风险发现列表
        raw_response: LLM 原始响应文本（用于调试）
    """

    canon_risk_score: float = 0.0
    findings: list[LLMCanonFinding] = field(default_factory=list)
    raw_response: str = ""


# ── 常量 ─────────────────────────────────────────────────────────────────


_MAX_SNIPPET_LEN = 100
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_AUDIT_SYSTEM_PROMPT = "\n".join(
    [
        "你是一名专业的世界观规则审计员。请仔细阅读用户提供的文本和世界观规则，",
        "判断文本是否存在违反规则的情况。",
        "",
        "审计重点：",
        '1. 复杂语义和隐喻（如用"铁器"隐喻"武器"）',
        "2. 例外场景（规则允许的例外是否被正确标记）",
        "3. 角色行为是否暗含违反规则的能力或权限",
        "4. 文本描述是否与规则的内在逻辑冲突",
        "",
        "输出要求：",
        "- 只输出 JSON，不要任何解释或 Markdown 代码块",
        "- JSON 格式如下：",
        "{",
        '  "canon_risk_score": 0.0,',
        '  "findings": [',
        "    {",
        '      "rule_concept": "规则概念",',
        '      "detail": "详细说明违反点",',
        '      "severity": "high|medium|low",',
        '      "snippet": "相关文本片段"',
        "    }",
        "  ]",
        "}",
        "- canon_risk_score 必须是 0 到 1 之间的浮点数",
        "- 如果没有发现风险，findings 为空数组，canon_risk_score 为 0.0",
        "- severity 只能是 high、medium、low 之一",
    ]
)


# ── 辅助函数 ─────────────────────────────────────────────────────────────


def _format_rules_for_prompt(rules: list[CanonRule]) -> str:
    """将规则列表格式化为 prompt 中的文本。

    Args:
        rules: 世界观规则列表

    Returns:
        供 LLM 阅读的规则文本
    """
    if not rules:
        return "（无显式规则，仅做一般性世界观一致性审查）"

    lines: list[str] = []
    for i, rule in enumerate(rules, 1):
        lines.append(f"{i}. [{rule.rule_type}] {rule.concept}: {rule.constraint}")
    return "\n".join(lines)


def _extract_json_from_response(text: str) -> str:
    """从 LLM 响应中提取 JSON 片段。

    兼容纯 JSON 或被 Markdown 代码块包裹的情况。

    Args:
        text: LLM 原始响应

    Returns:
        提取出的 JSON 字符串
    """
    text = text.strip()
    # 尝试匹配 Markdown 代码块
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


def _clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """将浮点数限制在指定范围内。"""
    return max(min_val, min(max_val, value))


def _parse_audit_response(response_text: str) -> CanonAuditResult:
    """解析 LLM 的 JSON 响应为 CanonAuditResult。

    Args:
        response_text: LLM 原始响应文本

    Returns:
        解析后的审计结果，解析失败时返回空结果
    """
    raw_response = response_text
    json_text = _extract_json_from_response(response_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.warning("LLM Canon 审计响应解析失败: %s", e)
        return CanonAuditResult(raw_response=raw_response)

    if not isinstance(data, dict):
        logger.warning("LLM Canon 审计响应不是 JSON 对象")
        return CanonAuditResult(raw_response=raw_response)

    score = _clamp(float(data.get("canon_risk_score", 0.0)))

    findings: list[LLMCanonFinding] = []
    raw_findings = data.get("findings", [])
    if isinstance(raw_findings, list):
        for item in raw_findings:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "medium")).lower()
            if severity not in {"high", "medium", "low"}:
                severity = "medium"
            snippet = str(item.get("snippet", ""))[:_MAX_SNIPPET_LEN]
            findings.append(
                LLMCanonFinding(
                    rule_concept=str(item.get("rule_concept", "")),
                    detail=str(item.get("detail", "")),
                    severity=severity,
                    snippet=snippet,
                )
            )

    # 如果没有发现但分数异常高，按发现数量修正分数
    if not findings:
        score = 0.0

    # 按严重程度排序
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 9))

    return CanonAuditResult(
        canon_risk_score=score,
        findings=findings,
        raw_response=raw_response,
    )


# ── LLMCanonAuditor ──────────────────────────────────────────────────────


class LLMCanonAuditor:
    """LLM 二次世界观规则审计器。

    只标记不阻断，用于发现 CanonChecker 难以检测的复杂语义违规。

    Attributes:
        llm_bus: LLM 调用总线（可为 None，此时审计为空操作）
        rules: 默认使用的世界观规则列表
    """

    def __init__(
        self,
        llm_bus: LLMBus | None = None,
        rules: list[CanonRule] | None = None,
    ) -> None:
        """初始化审计器。

        Args:
            llm_bus: LLM 调用总线实例（可选）
            rules: 默认世界观规则列表（可选）
        """
        self.llm_bus = llm_bus
        self.rules = rules or []

    def audit_text(
        self,
        text: str,
        rules: list[CanonRule] | None = None,
        chapter_id: str = "",
    ) -> CanonAuditResult:
        """对文本执行 LLM Canon 审计。

        Args:
            text: 待审计的章节文本
            rules: 本次审计使用的规则列表（覆盖默认规则）
            chapter_id: 章节 ID，用于日志和指标追踪

        Returns:
            CanonAuditResult，LLMBus 不可用时返回空结果
        """
        if self.llm_bus is None:
            logger.debug("LLM Canon 审计: LLMBus 不可用，跳过")
            return CanonAuditResult()

        effective_rules = rules if rules is not None else self.rules
        if not effective_rules and not self.rules:
            logger.debug("LLM Canon 审计: 无规则，跳过")
            return CanonAuditResult()

        prompt_text = (
            f"【世界观规则】\n{_format_rules_for_prompt(effective_rules)}\n\n"
            f"【待审计文本】\n{text}\n\n"
            "请只输出 JSON 格式的审计结果。"
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _AUDIT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ]

        try:
            response = self.llm_bus.chat(
                messages=messages,
                temperature=0.1,
                max_tokens=1500,
                chapter_id=chapter_id,
            )
        except Exception as e:
            logger.warning("LLM Canon 审计调用失败: %s", e)
            return CanonAuditResult()

        response_text = ""
        try:
            response_text = response.choices[0].message.content or ""
        except (AttributeError, IndexError, KeyError) as e:
            logger.warning("LLM Canon 审计无法提取响应文本: %s", e)
            return CanonAuditResult()

        if not response_text.strip():
            return CanonAuditResult()

        return _parse_audit_response(response_text)

    def __repr__(self) -> str:
        return (
            f"LLMCanonAuditor(rules={len(self.rules)}, llm_bus={'set' if self.llm_bus else 'None'})"
        )
