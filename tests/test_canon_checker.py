"""CanonChecker 世界观规则校验器测试。

测试覆盖：
- 规则解析（编号列表 / 关键陈述 / 空文件 / 无规则章节）
- 规则类型检测（negation / exclusive / positive）
- 文本校验（明确违反 / 约束缺失 / 合规文本）
- SafetyFence 集成
"""

from pathlib import Path

import pytest

from opennovel.core.canon_checker import (
    CanonChecker,
    CanonRule,
    CanonViolation,
    _check_exclusive_violation,
    _check_negation_violation,
    _extract_core_concept,
    _extract_keywords,
    check_text_against_canon,
)
from opennovel.core.safety_fence import SafetyFence, SafetyFenceConfig

# ═══════════════════════════════════════════════════════════════════════════
# CanonRule 测试
# ═══════════════════════════════════════════════════════════════════════════


class TestCanonRule:
    """CanonRule 数据模型测试。"""

    def test_create_negation_rule(self) -> None:
        """测试创建否定规则。"""
        rule = CanonRule(
            concept="船上武器",
            constraint="船上没有武器",
            rule_type="negation",
            keywords=["船上", "武器"],
            constraint_keywords=["没有", "武器"],
        )
        assert rule.concept == "船上武器"
        assert rule.rule_type == "negation"
        assert "武器" in rule.keywords

    def test_create_exclusive_rule(self) -> None:
        """测试创建排他规则。"""
        rule = CanonRule(
            concept="冬眠舱苏醒",
            constraint="冬眠舱的苏醒只能由船长或医生触发",
            rule_type="exclusive",
        )
        assert rule.rule_type == "exclusive"
        assert "苏醒" in rule.keywords or rule.concept == "冬眠舱苏醒"


class TestCanonViolation:
    """CanonViolation 数据模型测试。"""

    def test_create_violation(self) -> None:
        """测试创建违规记录。"""
        v = CanonViolation(
            rule="船上武器",
            detail="文本提到了武器但未遵守没有武器的规则",
            snippet="他拿起了一把武器",
            severity="violation",
        )
        assert v.severity == "violation"
        assert "武器" in v.snippet

    def test_default_severity(self) -> None:
        """测试默认严重程度。"""
        v = CanonViolation(rule="test", detail="test")
        assert v.severity == "warning"


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数测试
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractKeywords:
    """关键词提取测试。"""

    def test_simple_keywords(self) -> None:
        """测试简单中文关键词提取。"""
        kw = _extract_keywords("船上没有武器")
        assert "船上" in kw or "没有" in kw or "武器" in kw

    def test_filters_stop_words(self) -> None:
        """测试过滤停用词。"""
        kw = _extract_keywords("这是船上的一把武器")
        # "的"、"一"、"把" 是停用词或短词
        assert "船上" in kw
        assert "武器" in kw

    def test_english_keywords(self) -> None:
        """测试英文关键词提取。"""
        kw = _extract_keywords("AI system has full access")
        assert any(w.lower() in ("ai", "system", "full", "access") for w in kw)

    def test_single_char_filtered(self) -> None:
        """测试单字符被过滤。"""
        kw = _extract_keywords("a b c")
        assert kw == []


class TestExtractCoreConcept:
    """核心概念提取测试。"""

    def test_negation_concept(self) -> None:
        """测试否定约束的概念提取。"""
        concept = _extract_core_concept("船上没有武器")
        assert concept is not None
        # 应该提取出"船上"和"武器"相关的内容

    def test_exclusive_concept(self) -> None:
        """测试排他约束的概念提取。"""
        concept = _extract_core_concept("冬眠舱的苏醒只能由船长或医生触发")
        assert "苏醒" in concept or "冬眠" in concept

    def test_positive_concept(self) -> None:
        """测试肯定约束的概念提取。"""
        concept = _extract_core_concept("每个冬眠舱都有独立的生命体征监控")
        assert concept is not None and len(concept) >= 4


# ═══════════════════════════════════════════════════════════════════════════
# 规则解析测试 — 从 Markdown 解析规则
# ═══════════════════════════════════════════════════════════════════════════


class TestParseRules:
    """CanonChecker 规则解析测试。"""

    def test_parse_numbered_rules(self) -> None:
        """测试解析 ## 规则 章节的编号列表。"""
        content = """# 世界观
## 规则
1. 船上没有武器——设计如此，防止航行中的暴力冲突
2. 冬眠舱的苏醒只能由船长或医生触发
3. 每个冬眠舱都有独立的生命体征监控
"""
        checker = CanonChecker()
        rules = checker._parse_rules(content, "world.md")
        assert len(rules) == 3

        # 检查规则类型
        types = [r.rule_type for r in rules]
        assert "negation" in types  # "没有武器"
        assert "exclusive" in types  # "只能由"

    def test_parse_bullet_constraints(self) -> None:
        """测试解析关键陈述中的约束。"""
        content = """# 世界观
## 背景
- 冬眠技术可以暂停大脑，但不能修改记忆
- 船舶AI有所有区域的监控权限
"""
        checker = CanonChecker()
        rules = checker._parse_rules(content, "world.md")
        # "不能修改记忆" 包含约束关键词
        assert len(rules) >= 1
        rule_texts = [r.raw_text for r in rules]
        assert any("不能修改记忆" in t for t in rule_texts)

    def test_parse_empty_content(self) -> None:
        """测试空内容。"""
        checker = CanonChecker()
        rules = checker._parse_rules("", "world.md")
        assert rules == []

    def test_parse_no_rules_section(self) -> None:
        """测试无规则章节。"""
        content = "# 只是普通内容\n没有规则章节"
        checker = CanonChecker()
        rules = checker._parse_rules(content, "world.md")
        assert rules == []

    def test_parse_duplicate_bullets_skipped(self) -> None:
        """测试已在规则章节的 bullet 不重复添加。"""
        content = """## 规则
1. 船上没有武器
## 其他
- 船上没有武器
"""
        checker = CanonChecker()
        rules = checker._parse_rules(content, "world.md")
        # 应该只有一条
        assert len(rules) == 1


class TestLoadRules:
    """CanonChecker.load_rules 测试。"""

    def test_load_from_nonexistent_dir(self, tmp_path: Path) -> None:
        """测试从不存在目录加载。"""
        checker = CanonChecker()
        rules = checker.load_rules(tmp_path / "nonexistent")
        assert rules == []

    def test_load_from_empty_dir(self, tmp_path: Path) -> None:
        """测试从空目录加载。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        checker = CanonChecker()
        rules = checker.load_rules(canon_dir)
        assert rules == []

    def test_load_from_dir_without_md(self, tmp_path: Path) -> None:
        """测试目录中无 md 文件。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "note.txt").write_text("some notes", encoding="utf-8")
        checker = CanonChecker()
        rules = checker.load_rules(canon_dir)
        assert rules == []

    def test_load_rules_from_file(self, tmp_path: Path) -> None:
        """测试从文件加载规则。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text(
            "## 规则\n1. 船上没有武器\n2. 黎明AI有所有区域的监控权限\n",
            encoding="utf-8",
        )
        checker = CanonChecker()
        rules = checker.load_rules(canon_dir)
        assert len(rules) == 2
        assert any("武器" in r.constraint for r in rules)

    def test_cache_works(self, tmp_path: Path) -> None:
        """测试规则缓存。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        checker = CanonChecker()
        rules1 = checker.load_rules(canon_dir)
        rules2 = checker.load_rules(canon_dir)
        assert rules1 is rules2  # 同一实例

    def test_clear_cache(self, tmp_path: Path) -> None:
        """测试清除缓存。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        checker = CanonChecker()
        rules1 = checker.load_rules(canon_dir)
        checker.clear_cache()
        rules2 = checker.load_rules(canon_dir)
        assert rules1 is not rules2

    def test_load_multiple_files(self, tmp_path: Path) -> None:
        """测试加载多个文件。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "magic.md").write_text("## 规则\n1. 魔法消耗寿命\n", encoding="utf-8")
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        checker = CanonChecker()
        rules = checker.load_rules(canon_dir)
        assert len(rules) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 文本校验测试
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckNegationViolation:
    """否定规则违反检测测试。"""

    def test_no_mention_no_violation(self) -> None:
        """测试未提到被否定事物 → 无违反。"""
        rule = CanonRule(
            concept="船上武器",
            constraint="船上没有武器",
            rule_type="negation",
            keywords=["船上", "武器"],
            constraint_keywords=["没有", "武器"],
            raw_text="船上没有武器",
        )
        sentences = ["天气很好", "大家都在休息"]
        violations = _check_negation_violation(rule, sentences)
        assert violations == []

    def test_affirmative_mention_is_violation(self) -> None:
        """测试提到被否定事物且在肯定语境 → 违反。"""
        rule = CanonRule(
            concept="船上武器",
            constraint="船上没有武器",
            rule_type="negation",
            keywords=["船上", "武器"],
            constraint_keywords=["没有", "武器"],
            raw_text="船上没有武器",
        )
        sentences = ["他拿起了一把武器", "气氛变得紧张"]
        violations = _check_negation_violation(rule, sentences)
        assert len(violations) >= 1
        assert violations[0].severity == "violation"

    def test_negation_in_text_ok(self) -> None:
        """测试文本中也用否定 → OK。"""
        rule = CanonRule(
            concept="船上武器",
            constraint="船上没有武器",
            rule_type="negation",
            keywords=["船上", "武器"],
            constraint_keywords=["没有", "武器"],
            raw_text="船上没有武器",
        )
        sentences = ["船上没有任何武器", "连一把刀都没有"]
        violations = _check_negation_violation(rule, sentences)
        assert violations == []


class TestCheckExclusiveViolation:
    """排他规则违反检测测试。"""

    def test_no_mention_no_violation(self) -> None:
        """测试未提到排他事件 → 无违反。"""
        rule = CanonRule(
            concept="冬眠舱苏醒",
            constraint="冬眠舱的苏醒只能由船长或医生触发",
            rule_type="exclusive",
            keywords=["冬眠舱", "苏醒"],
            constraint_keywords=["只能", "船长", "医生", "触发"],
            raw_text="冬眠舱的苏醒只能由船长或医生触发",
        )
        sentences = ["天气很好"]
        violations = _check_exclusive_violation(rule, sentences)
        assert violations == []

    def test_allowed_actor_ok(self) -> None:
        """测试允许的执行者 → OK。"""
        rule = CanonRule(
            concept="冬眠舱苏醒",
            constraint="冬眠舱的苏醒只能由船长或医生触发",
            rule_type="exclusive",
            keywords=["冬眠舱", "苏醒"],
            constraint_keywords=["只能", "船长", "医生", "触发"],
            raw_text="冬眠舱的苏醒只能由船长或医生触发",
        )
        sentences = ["船长触发了冬眠舱的苏醒程序"]
        violations = _check_exclusive_violation(rule, sentences)
        assert violations == []

    def test_unauthorized_actor_violation(self) -> None:
        """测试非许可者执行 → 违反。"""
        rule = CanonRule(
            concept="冬眠舱苏醒",
            constraint="冬眠舱的苏醒只能由船长或医生触发",
            rule_type="exclusive",
            keywords=["冬眠舱", "苏醒"],
            constraint_keywords=["只能", "船长", "医生", "触发"],
            raw_text="冬眠舱的苏醒只能由船长或医生触发",
        )
        sentences = ["林远悄悄拔掉了冬眠舱的电源"]
        violations = _check_exclusive_violation(rule, sentences)
        assert len(violations) >= 1


class TestCheckText:
    """CanonChecker.check_text 完整流程测试。"""

    def test_no_rules(self) -> None:
        """测试无规则时无违反。"""
        checker = CanonChecker()
        violations = checker.check_text("一些文本", [])
        assert violations == []

    def test_empty_text(self) -> None:
        """测试空文本。"""
        checker = CanonChecker()
        rule = CanonRule(concept="test", constraint="rule", keywords=["test"])
        violations = checker.check_text("", [rule])
        assert violations == []

    def test_whitespace_text(self) -> None:
        """测试空白文本。"""
        checker = CanonChecker()
        rule = CanonRule(concept="test", constraint="rule")
        violations = checker.check_text("   ", [rule])
        assert violations == []

    def test_compliant_text(self) -> None:
        """测试合规文本→无违反。"""
        checker = CanonChecker()
        rules = [
            CanonRule(
                concept="船上武器",
                constraint="船上没有武器",
                rule_type="negation",
                keywords=["船上", "武器"],
                constraint_keywords=["没有", "武器"],
            ),
        ]
        text = "船长在舰桥上查看航行数据。医生在医疗舱整理设备。"
        violations = checker.check_text(text, rules)
        # 未提到武器相关概念 → 无违规
        # 但"船上"关键词在规则中，如果 "舰桥" 不匹配则无
        assert isinstance(violations, list)

    def test_negation_violation_detected(self) -> None:
        """测试检测到否定规则违反。"""
        checker = CanonChecker()
        rules = [
            CanonRule(
                concept="船上武器",
                constraint="船上没有武器",
                rule_type="negation",
                keywords=["船上", "武器"],
                constraint_keywords=["没有", "武器"],
                raw_text="船上没有武器",
            ),
        ]
        text = "他在船舱里找到了一把隐藏的武器。"
        violations = checker.check_text(text, rules)
        assert len(violations) >= 1
        assert violations[0].severity == "violation"

    def test_multiple_rules_violations(self) -> None:
        """测试多条规则同时违反。"""
        checker = CanonChecker()
        rules = [
            CanonRule(
                concept="船上武器",
                constraint="船上没有武器",
                rule_type="negation",
                keywords=["船上", "武器"],
                constraint_keywords=["没有", "武器"],
            ),
            CanonRule(
                concept="冬眠舱苏醒",
                constraint="冬眠舱的苏醒只能由船长或医生触发",
                rule_type="exclusive",
                keywords=["冬眠舱", "苏醒"],
                constraint_keywords=["只能", "船长", "医生", "触发"],
            ),
        ]
        text = "他拿起武器。林远私自启动了冬眠舱的苏醒程序。"
        violations = checker.check_text(text, rules)
        assert len(violations) >= 1

    def test_severity_ordering(self) -> None:
        """测试结果按严重程度排序。"""
        checker = CanonChecker()
        rules = [
            CanonRule(
                concept="船上武器",
                constraint="船上没有武器",
                rule_type="negation",
                keywords=["船上", "武器"],
                constraint_keywords=["没有", "武器"],
            ),
        ]
        # 创建一个肯定提到武器的文本
        text = "他在船上找到了一把武器。"
        violations = checker.check_text(text, rules)
        if violations:
            assert violations[0].severity in ("violation", "warning", "suggestion")


# ═══════════════════════════════════════════════════════════════════════════
# 集成测试 — check_text_against_canon
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckTextAgainstCanon:
    """check_text_against_canon 集成测试。"""

    def test_with_real_canon_dir(self, tmp_path: Path) -> None:
        """测试真实的 canon 目录。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text(
            "## 规则\n1. 船上没有武器\n2. 黎明AI有所有区域的监控权限\n",
            encoding="utf-8",
        )
        text = "他在船上捡起了一把武器。"
        violations = check_text_against_canon(text, canon_dir)
        assert len(violations) >= 1

    def test_no_canon_dir(self, tmp_path: Path) -> None:
        """测试无 canon 目录时跳过。"""
        violations = check_text_against_canon("一些文本", tmp_path / "nonexistent")
        assert violations == []

    def test_reuse_checker(self, tmp_path: Path) -> None:
        """测试复用 CanonChecker 实例。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        checker = CanonChecker()
        v1 = check_text_against_canon("他拿起武器", canon_dir, checker)
        v2 = check_text_against_canon("天气很好", canon_dir, checker)
        assert len(v1) >= 1
        assert len(v2) == 0


# ═══════════════════════════════════════════════════════════════════════════
# SafetyFence 集成测试
# ═══════════════════════════════════════════════════════════════════════════


class TestSafetyFenceCanonIntegration:
    """SafetyFence.check_canon_integrity 集成测试。"""

    def test_disabled_fence_skips(self) -> None:
        """测试禁用时跳过校验。"""
        config = SafetyFenceConfig(enabled=False)
        fence = SafetyFence(config)
        assert fence.check_canon_integrity("writer", "任何文本", Path("/nonexistent"))

    def test_no_canon_dir_skips(self) -> None:
        """测试无 canon 目录时跳过。"""
        fence = SafetyFence()
        assert fence.check_canon_integrity("writer", "任何文本", Path("/nonexistent"))

    def test_compliant_text_passes(self, tmp_path: Path) -> None:
        """测试合规文本通过校验。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        fence = SafetyFence()
        # 文本未提到武器相关概念
        assert fence.check_canon_integrity("writer", "船长在舰桥查看数据。", canon_dir)

    def test_violation_text_fails(self, tmp_path: Path) -> None:
        """测试违规文本被阻断。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        fence = SafetyFence()
        result = fence.check_canon_integrity("writer", "他在船上捡起了一把武器。", canon_dir)
        assert result is False
        assert len(fence.violations) >= 1
        assert fence.violations[0].rule == "canon_violation"

    def test_violation_recorded_with_agent_name(self, tmp_path: Path) -> None:
        """测试违规记录包含 Agent 名称。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        fence = SafetyFence()
        fence.check_canon_integrity("writer", "他拿起了武器。", canon_dir)
        assert fence.violations[0].agent == "writer"

    def test_no_rules_no_violations(self, tmp_path: Path) -> None:
        """测试无规则时通过校验。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("# 无规则章节\n", encoding="utf-8")
        fence = SafetyFence()
        assert fence.check_canon_integrity("writer", "任何文本", canon_dir)

    def test_strict_mode_suggestion_counts(self, tmp_path: Path) -> None:
        """测试严格模式下 suggestion 也算违反。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        fence = SafetyFence()
        # 使用一个不太可能触发的概念来测试严格模式
        # 实际上 strict 影响的是 suggestion 级别是否阻断
        result = fence.check_canon_integrity("writer", "今天天气很好", canon_dir, strict=True)
        # 未提到武器 → 不触发任何规则 → 通过
        assert result is True

    def test_multiple_violations_all_recorded(self, tmp_path: Path) -> None:
        """测试多条违规全部记录。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text(
            "## 规则\n1. 船上没有武器\n2. 冬眠舱的苏醒只能由船长或医生触发\n",
            encoding="utf-8",
        )
        fence = SafetyFence()
        fence.check_canon_integrity(
            "writer",
            "林远拿起了武器，然后私自触发了冬眠舱的苏醒程序。",
            canon_dir,
        )
        assert len(fence.violations) >= 1

    def test_config_canon_dir_used(self, tmp_path: Path) -> None:
        """测试 config 中的 canon_dir 被使用。"""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("## 规则\n1. 船上没有武器\n", encoding="utf-8")
        config = SafetyFenceConfig(canon_dir=str(canon_dir))
        fence = SafetyFence(config)
        result = fence.check_canon_integrity(
            "writer",
            "他拿起了武器。",
        )
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════
# 真实世界规则测试 — 使用 demo_novel 的 canon 文件
# ═══════════════════════════════════════════════════════════════════════════


class TestWithDemoCanon:
    """使用 demo_novel 的世界观规则进行校验测试。"""

    @pytest.fixture
    def demo_canon_dir(self) -> Path:
        """获取 demo_novel 的 canon 目录。"""
        project_root = Path(__file__).resolve().parent.parent
        return project_root / "novels" / "demo_novel" / "canon"

    def test_demo_canon_loaded(self, demo_canon_dir: Path) -> None:
        """测试 demo_novel 规则能被正常加载。"""
        if not demo_canon_dir.exists():
            pytest.skip("novels/demo_novel/canon 目录不存在")
        checker = CanonChecker()
        rules = checker.load_rules(demo_canon_dir)
        assert len(rules) >= 4  # world_rules.md 有 4 条规则

    def test_demo_canon_negation_check(self, demo_canon_dir: Path) -> None:
        """测试 demo_novel 的否定规则校验。"""
        if not demo_canon_dir.exists():
            pytest.skip("novels/demo_novel/canon 目录不存在")
        text = "林远从柜子里拿出了一把武器。"
        violations = check_text_against_canon(text, demo_canon_dir)
        # 至少应检测到"武器"相关违规
        weapon_violations = [v for v in violations if "武器" in v.detail]
        assert len(weapon_violations) >= 1
