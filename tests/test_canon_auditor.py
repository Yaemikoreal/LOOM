"""LLMCanonAuditor 测试。

测试覆盖：
- LLMBus 不可用时返回空结果
- LLMBus 可用时正确解析 JSON 响应
- 非 JSON / Markdown 代码块 / 无效响应的容错
- 规则格式化辅助函数
"""

from typing import Any
from unittest.mock import MagicMock

from opennovel.core.canon_auditor import (
    CanonAuditResult,
    LLMCanonAuditor,
    LLMCanonFinding,
    _extract_json_from_response,
    _format_rules_for_prompt,
    _parse_audit_response,
)
from opennovel.core.canon_checker import CanonRule


class MockMessage:
    """模拟 LLM 响应消息。"""

    def __init__(self, content: str) -> None:
        self.content = content


class MockChoice:
    """模拟 LLM 响应 choice。"""

    def __init__(self, content: str) -> None:
        self.message = MockMessage(content)


class MockLLMResponse:
    """模拟 LiteLLM 响应对象。"""

    def __init__(self, content: str) -> None:
        self.choices = [MockChoice(content)]


class MockLLMBus:
    """模拟 LLMBus，可预设响应内容。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.call_count = 0
        self.last_messages: list[dict[str, str]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> MockLLMResponse:
        self.call_count += 1
        self.last_messages = messages
        return MockLLMResponse(self.content)


def make_rule(
    concept: str = "船上武器",
    constraint: str = "船上没有武器",
    rule_type: str = "negation",
) -> CanonRule:
    """构造测试规则。"""
    return CanonRule(
        concept=concept,
        constraint=constraint,
        rule_type=rule_type,
        keywords=["船上", "武器"],
        raw_text=constraint,
    )


class TestLLMCanonAuditorNoLLM:
    """LLMBus 不可用时的行为测试。"""

    def test_no_llm_bus_returns_empty_result(self) -> None:
        """无 LLMBus 时返回空结果。"""
        auditor = LLMCanonAuditor(llm_bus=None, rules=[make_rule()])
        result = auditor.audit_text("他拿起武器")

        assert isinstance(result, CanonAuditResult)
        assert result.canon_risk_score == 0.0
        assert result.findings == []

    def test_no_rules_returns_empty_result(self) -> None:
        """无规则时返回空结果（避免浪费 LLM 调用）。"""
        mock_bus = MockLLMBus('{"canon_risk_score": 0.0, "findings": []}')
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[])
        result = auditor.audit_text("他拿起武器")

        assert result.canon_risk_score == 0.0
        assert result.findings == []
        assert mock_bus.call_count == 0


class TestLLMCanonAuditorWithLLM:
    """LLMBus 可用时的审计行为测试。"""

    def test_parses_risk_score_and_findings(self) -> None:
        """正确解析 risk_score 和 findings。"""
        response = (
            '{"canon_risk_score": 0.75, "findings": ['
            '{"rule_concept": "船上武器", "detail": "文本提到武器", '
            '"severity": "high", "snippet": "他拿起武器"}'
            "]}"
        )
        mock_bus = MockLLMBus(response)
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("他拿起武器", chapter_id="ch_001")

        assert mock_bus.call_count == 1
        assert result.canon_risk_score == 0.75
        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.rule_concept == "船上武器"
        assert finding.severity == "high"
        assert "他拿起武器" in finding.snippet

    def test_returns_empty_when_no_risk(self) -> None:
        """无风险时返回空 findings 和 0 分。"""
        response = '{"canon_risk_score": 0.0, "findings": []}'
        mock_bus = MockLLMBus(response)
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("天气很好")

        assert result.canon_risk_score == 0.0
        assert result.findings == []

    def test_clamps_risk_score_to_range(self) -> None:
        """risk_score 被限制在 [0, 1]。"""
        response = '{"canon_risk_score": 1.5, "findings": ['
        response += '{"rule_concept": "x", "detail": "y", "severity": "medium"}'
        response += "]}"
        mock_bus = MockLLMBus(response)
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("文本")

        assert result.canon_risk_score == 1.0

    def test_no_findings_resets_score(self) -> None:
        """findings 为空时强制 risk_score 为 0。"""
        response = '{"canon_risk_score": 0.9, "findings": []}'
        mock_bus = MockLLMBus(response)
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("文本")

        assert result.canon_risk_score == 0.0

    def test_invalid_severity_defaults_to_medium(self) -> None:
        """无效 severity 默认 medium。"""
        response = (
            '{"canon_risk_score": 0.5, "findings": ['
            '{"rule_concept": "x", "detail": "y", "severity": "unknown"}'
            "]}"
        )
        mock_bus = MockLLMBus(response)
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("文本")

        assert result.findings[0].severity == "medium"

    def test_response_wrapped_in_markdown_code_block(self) -> None:
        """兼容 Markdown 代码块包裹的 JSON。"""
        response = (
            "```json\n"
            '{"canon_risk_score": 0.6, "findings": ['
            '{"rule_concept": "x", "detail": "y", "severity": "low"}'
            "]}\n"
            "```"
        )
        mock_bus = MockLLMBus(response)
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("文本")

        assert result.canon_risk_score == 0.6
        assert len(result.findings) == 1

    def test_invalid_json_returns_empty_result(self) -> None:
        """无效 JSON 时返回空结果，不抛异常。"""
        mock_bus = MockLLMBus("这不是 JSON")
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("文本")

        assert result.canon_risk_score == 0.0
        assert result.findings == []
        assert result.raw_response == "这不是 JSON"

    def test_llm_call_failure_returns_empty_result(self) -> None:
        """LLM 调用异常时返回空结果。"""
        mock_bus = MagicMock()
        mock_bus.chat.side_effect = RuntimeError("网络错误")
        auditor = LLMCanonAuditor(llm_bus=mock_bus, rules=[make_rule()])
        result = auditor.audit_text("文本")

        assert result.canon_risk_score == 0.0
        assert result.findings == []


class TestParseAuditResponse:
    """_parse_audit_response 解析细节测试。"""

    def test_extract_json_from_plain_text(self) -> None:
        """从纯文本中提取 JSON。"""
        text = '{"canon_risk_score": 0.1, "findings": []}'
        assert _extract_json_from_response(text) == text

    def test_extract_json_from_fenced_block(self) -> None:
        """从 Markdown 代码块中提取 JSON。"""
        text = '```json\n{"a": 1}\n```'
        assert _extract_json_from_response(text) == '{"a": 1}'

    def test_parse_audit_response_sorts_findings(self) -> None:
        """findings 按严重程度排序。"""
        text = (
            '{"canon_risk_score": 0.8, "findings": ['
            '{"rule_concept": "low", "detail": "d", "severity": "low"},'
            '{"rule_concept": "high", "detail": "d", "severity": "high"}'
            "]}"
        )
        result = _parse_audit_response(text)
        assert result.findings[0].severity == "high"
        assert result.findings[1].severity == "low"


class TestFormatRulesForPrompt:
    """_format_rules_for_prompt 测试。"""

    def test_empty_rules(self) -> None:
        """空规则列表返回提示文本。"""
        text = _format_rules_for_prompt([])
        assert "无显式规则" in text

    def test_multiple_rules(self) -> None:
        """多条规则按编号格式化。"""
        rules = [
            make_rule("船上武器", "船上没有武器", "negation"),
            make_rule("冬眠舱", "冬眠舱只能由船长触发", "exclusive"),
        ]
        text = _format_rules_for_prompt(rules)
        assert "1. [negation]" in text
        assert "2. [exclusive]" in text
        assert "船上没有武器" in text


class TestFindingModel:
    """LLMCanonFinding 数据模型测试。"""

    def test_default_severity(self) -> None:
        """默认 severity 为 medium。"""
        finding = LLMCanonFinding(rule_concept="x", detail="y")
        assert finding.severity == "medium"

    def test_snippet_truncation_on_parse(self) -> None:
        """snippet 过长时被截断。"""
        long_snippet = "x" * 200
        response = (
            '{"canon_risk_score": 0.5, "findings": ['
            f'{{"rule_concept": "x", "detail": "y", "severity": "medium", '
            f'"snippet": "{long_snippet}"}}'
            "]}"
        )
        result = _parse_audit_response(response)
        assert len(result.findings[0].snippet) <= 100
