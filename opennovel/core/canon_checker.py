"""世界观规则校验器 — Canon 不可违背检查。

基于 ADR 0006 安全围栏设计中的"Canon 不可违背"原则，
从 canon/ 目录中的 Markdown 文件提取世界观规则，
对 Agent 生成的文本进行轻量级规则违反检测。

使用方式:
    checker = CanonChecker()
    rules = checker.load_rules(project_root / "canon")
    violations = checker.check_text("生成的文本段落...", rules)
    if violations:
        for v in violations:
            print(f"[{v.severity}] {v.detail}")

依赖: 无（纯 Python 标准库实现，关键词匹配，不依赖 LLM）
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 规则模型 ─────────────────────────────────────────────────────────────


@dataclass
class CanonRule:
    """一条世界观规则。

    Attributes:
        concept: 主题概念（如"船上武器"、"冬眠技术"）
        constraint: 约束描述（如"船上没有武器"）
        rule_type: 规则类型（negation / exclusive / positive / conditional）
        source_file: 来源文件名
        keywords: 用于在文本中检测的关键词列表（概念相关）
        constraint_keywords: 约束关键词列表（用于验证遵守情况）
        raw_text: 原始规则文本
    """

    concept: str
    constraint: str
    rule_type: str = "positive"  # negation / exclusive / positive / conditional
    source_file: str = ""
    keywords: list[str] = field(default_factory=list)
    constraint_keywords: list[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class CanonViolation:
    """世界观违规记录。

    Attributes:
        rule: 违反的规则概念
        detail: 违规详情
        snippet: 违规文本片段（最多 80 字）
        severity: 严重程度（suggestion / warning / violation）
    """

    rule: str
    detail: str
    snippet: str = ""
    severity: str = "warning"  # suggestion / warning / violation


# ── 规则类型检测模式 ─────────────────────────────────────────────────────

# 否定约束模式：X没有Y / X不能Y / X不得Y / X禁止Y
_NEGATION_PATTERNS = [
    (r"(没有|无|不存在)\s*(.+)", "negation"),
    (r"(不能|不可以|不得|禁止)\s*(.+)", "negation"),
]

# 排他约束模式：X只能Y / X必须由Z
_EXCLUSIVE_PATTERNS = [
    (r"只能\s*(?:由|通过)?\s*(.+)", "exclusive"),
    (r"必须\s*(?:由|通过)?\s*(.+)", "exclusive"),
    (r"只有\s*(.+?)\s*(?:才能|才可)", "exclusive"),
]

# 条件约束模式：如果X则Y / X时Y
_CONDITIONAL_PATTERNS = [
    (r"(?:如果|若|当)\s*(.+?)\s*[，,]\s*(.+)", "conditional"),
]

# 规则章节标题匹配
_RULE_SECTION_RE = re.compile(r"##\s*规则\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)

# 编号列表项匹配
_NUMBERED_ITEM_RE = re.compile(r"^\d+[.、]\s*(.+)$", re.MULTILINE)

# 关键陈述列表项匹配
_BULLET_ITEM_RE = re.compile(r"^[-*+]\s*(.+)$", re.MULTILINE)

# 关键词提取：中文/英文词汇，过滤标点和停用词
_KEYWORD_SPLIT_RE = re.compile(r'[，。、；：""（）()【】/\s]+')

# 停用词（不单独作为关键词）
_STOP_WORDS: set[str] = {
    "的",
    "了",
    "在",
    "是",
    "有",
    "和",
    "与",
    "也",
    "都",
    "就",
    "要",
    "会",
    "可",
    "以",
    "能",
    "及",
    "或",
    "但",
    "而",
    "且",
    "被",
    "把",
    "让",
    "给",
    "对",
    "从",
    "到",
    "向",
    "为",
    "由",
    "这",
    "那",
    "它",
    "他",
    "她",
    "们",
    "个",
    "之",
    "上",
    "下",
    "不",
    "没",
    "很",
    "太",
    "更",
    "最",
    "还",
    "又",
    "再",
    "已",
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "can",
    "could",
    "may",
    "might",
    "shall",
    "should",
    "to",
    "of",
    "in",
    "on",
    "at",
    "by",
    "for",
    "with",
    "as",
    "from",
    "into",
}


def _extract_keywords(text: str) -> list[str]:
    """从文本中提取关键词。

    按分隔符切分后过滤停用词和过短/过长的词汇。
    对于中文连续文本（无分隔符），按以下策略提取子关键词：
    1. 在中文连词（和/或/与）处切分
    2. 提取 2-3 字子串作为候选关键词

    Args:
        text: 输入文本

    Returns:
        关键词列表
    """
    # 第一步：按标点/分隔符切分
    parts = _KEYWORD_SPLIT_RE.split(text)
    base_keywords: list[str] = []
    for part in parts:
        part = part.strip().strip('"').strip("'")
        if not part:
            continue
        if len(part) < 2:
            continue
        if part.lower() in _STOP_WORDS:
            continue
        base_keywords.append(part)

    final_set: set[str] = set()

    for kw in base_keywords:
        final_set.add(kw)

        # 在中文连词处切分
        for conj in ["和", "或", "与", "及"]:
            if conj in kw:
                for sp in kw.split(conj):
                    sp = sp.strip()
                    if len(sp) >= 2:
                        final_set.add(sp)

        # 对较长的无分隔中文文本，提取 2-3 字子串
        # 只对纯中文文本（无英文）做此处理
        if len(kw) >= 4 and not any(c.isascii() and c.isalpha() for c in kw):
            _add_ngrams_to_set(kw, final_set, min_n=2, max_n=3)

    # 确保至少返回原始关键词（避免返回空列表）
    if not final_set and base_keywords:
        return base_keywords

    return sorted(final_set, key=len, reverse=True)


def _add_ngrams_to_set(text: str, result: set[str], min_n: int = 2, max_n: int = 3) -> None:
    """将文本的 n-gram 子串加入集合（不做停用词过滤）。

    n-gram 是子串匹配的候选关键词，单个字符在不同词中有不同含义，
    停用词过滤只适用于完整词级别的 _extract_keywords。

    Args:
        text: 输入文本
        result: 目标集合（原地修改）
        min_n: 最小 n-gram 长度
        max_n: 最大 n-gram 长度
    """
    for i in range(len(text)):
        for n in range(min_n, min(max_n + 1, len(text) - i + 1)):
            gram = text[i : i + n]
            if len(gram) < 2:
                continue
            result.add(gram)


def _get_rule_type(constraint: str) -> str:
    """检测规则类型。

    Args:
        constraint: 约束文本

    Returns:
        规则类型: negation / exclusive / conditional / positive
    """
    for patterns, rtype in [
        (_NEGATION_PATTERNS, "negation"),
        (_EXCLUSIVE_PATTERNS, "exclusive"),
        (_CONDITIONAL_PATTERNS, "conditional"),
    ]:
        for pattern, _ in patterns:
            if re.search(pattern, constraint):
                return rtype
    return "positive"


def _extract_core_concept(constraint: str) -> str:
    """从约束文本中提取核心概念。

    策略：取约束文本头部的名词短语，
    对于"没有X"格式取"X"，对于"只能由X"格式取"X"之前的描述。

    Args:
        constraint: 约束文本

    Returns:
        核心概念
    """
    # "船上没有武器" → "船上武器"
    # "冬眠舱的苏醒只能由船长或医生触发" → "冬眠舱苏醒"
    # "每个冬眠舱都有独立的生命体征监控" → "冬眠舱生命体征监控"

    for patterns, _ in [
        (_NEGATION_PATTERNS, None),
        (_EXCLUSIVE_PATTERNS, None),
    ]:
        for pattern, _ in patterns:
            m = re.search(pattern, constraint)
            if m:
                # 取否定/排他词前面的部分作为概念
                subject = constraint[: m.start()].strip()
                obj = (
                    m.group(2)
                    if pattern == _NEGATION_PATTERNS[0][0] or pattern == _NEGATION_PATTERNS[1][0]
                    else m.group(1)
                )
                # 组合主语和宾语
                if subject and obj:
                    # 去掉所有格"的"
                    subject_clean = re.sub(r"的$", "", subject)
                    return subject_clean + obj
                return subject or obj

    # 肯定陈述：取前十来个字
    return constraint[:20].strip().rstrip("，。、；：")


# ── 违规检测 ─────────────────────────────────────────────────────────────

# 违反肯定规则的检测模式：如果规则说"X有Y"，检查"X没有Y"或"X缺Y"
_VIOLATION_NEGATION_PATTERNS = [
    r"(?:没有|无|不存在|缺少|缺乏)\s*{obj}",
    r"{subj}\s*(?:没有|无|不存在|缺少|缺乏)",
]

# 明确肯定的词汇
_AFFIRMATION_WORDS = {"有", "是", "存在", "可以", "能", "会", "要"}


def _check_negation_violation(
    rule: CanonRule,
    sentences: list[str],
) -> list[CanonViolation]:
    """检查否定规则违反。

    规则格式"X没有Y"：如果文本说"X有Y"或"Y出现在X语境"，则违反。

    Args:
        rule: 规则
        sentences: 按句分割的文本

    Returns:
        违反列表
    """
    violations: list[CanonViolation] = []
    constraint = rule.constraint

    # 提取被否定的事物
    for pattern, _ in _NEGATION_PATTERNS:
        m = re.search(pattern, constraint)
        if not m:
            continue
        negated_thing = (
            m.group(2).strip() if m.lastindex and m.lastindex >= 2 else m.group(1).strip()
        )
        negated_keywords = _extract_keywords(negated_thing)

        if not negated_keywords:
            continue

        for sentence in sentences:
            sentence_lower = sentence.lower()

            # 检查被否定事物的关键词是否出现在句中
            thing_found = any(kw.lower() in sentence_lower for kw in negated_keywords)
            if not thing_found:
                continue

            # 检查是否在肯定语境
            is_negated_in_text = any(w in sentence_lower for w in ["没有", "无", "不存在", "禁止"])

            # 如果提到被否定事物但在肯定语境中 → 可能违反
            # 如果文本也用了否定 → OK（文本也遵守了规则）
            if not is_negated_in_text:
                # 有肯定的表述 → 违反否定规则
                snippet = _extract_snippet(sentence)
                violations.append(
                    CanonViolation(
                        rule=rule.concept,
                        detail=f"规则要求「{rule.constraint}」，但文本有肯定表述",
                        snippet=snippet,
                        severity="violation",
                    )
                )
                break

    return violations


def _check_exclusive_violation(
    rule: CanonRule,
    sentences: list[str],
) -> list[CanonViolation]:
    """检查排他规则违反。

    规则格式"X只能由Y"：如果文本说"X由Z（非Y）"或"X被Z（非Y）"则违反。

    Args:
        rule: 规则
        sentences: 按句分割的文本

    Returns:
        违反列表
    """
    violations: list[CanonViolation] = []
    constraint = rule.constraint

    for pattern, _ in _EXCLUSIVE_PATTERNS:
        m = re.search(pattern, constraint)
        if not m:
            continue
        permitted_actor = m.group(1).strip()
        permitted_keywords = _extract_keywords(permitted_actor)

        # 排他事件/动作的描述（"冬眠舱的苏醒"）
        subject = constraint[: m.start()].strip()

        for sentence in sentences:
            sentence_lower = sentence.lower()

            # 检查是否提到排他事件
            subject_keywords = _extract_keywords(subject)
            subject_found = (
                any(kw.lower() in sentence_lower for kw in subject_keywords)
                if subject_keywords
                else False
            )

            if not subject_found:
                continue

            # 检查是否出现了非许可的执行者
            # 如果句子中出现了主语关键词，但没有许可关键词
            permitted_found = any(kw.lower() in sentence_lower for kw in permitted_keywords)

            # 检查是否有其他执行者痕迹（暗示非许可者执行）
            unauthorized_hints = ["自己", "擅自", "私自", "偷偷", "独自", "悄悄", "私下"]
            has_unauthorized_hint = any(w in sentence_lower for w in unauthorized_hints)

            if not permitted_found:
                if has_unauthorized_hint:
                    # 有非许可执行者的明确提示 → violation
                    snippet = _extract_snippet(sentence)
                    violations.append(
                        CanonViolation(
                            rule=rule.concept,
                            detail=f"规则要求「{rule.constraint}」，但文本疑似由非许可者执行",
                            snippet=snippet,
                            severity="violation",
                        )
                    )
                    break
                else:
                    # 未提及执行者但事件在发生 → suggestion
                    snippet = _extract_snippet(sentence)
                    violations.append(
                        CanonViolation(
                            rule=rule.concept,
                            detail=f"规则要求「{rule.constraint}」，但文本未明确由许可者执行",
                            snippet=snippet,
                            severity="suggestion",
                        )
                    )
                    break
                snippet = _extract_snippet(sentence)
                violations.append(
                    CanonViolation(
                        rule=rule.concept,
                        detail=f"规则要求「{rule.constraint}」，但文本疑似由非许可者执行",
                        snippet=snippet,
                        severity="violation",
                    )
                )
                break

    return violations


def _check_missing_constraint(
    rule: CanonRule,
    sentences: list[str],
) -> list[CanonViolation]:
    """检查是否提到了规则概念但未提及约束。

    用于产生 suggestion 级别的提醒。

    Args:
        rule: 规则
        sentences: 按句分割的文本

    Returns:
        违反列表
    """
    violations: list[CanonViolation] = []

    for sentence in sentences:
        sentence_lower = sentence.lower()

        # 检查概念关键词
        concept_found = any(kw.lower() in sentence_lower for kw in rule.keywords)
        if not concept_found:
            continue

        # 检查约束关键词
        constraint_found = any(kw.lower() in sentence_lower for kw in rule.constraint_keywords)
        if not constraint_found:
            snippet = _extract_snippet(sentence)
            violations.append(
                CanonViolation(
                    rule=rule.concept,
                    detail=f"文本提到了「{rule.concept}」相关内容，但未提及约束: {rule.constraint}",
                    snippet=snippet,
                    severity="suggestion",
                )
            )
            break  # 每规则只记一条

    return violations


def _extract_snippet(text: str, max_len: int = 80) -> str:
    """提取文本片段，限制最大长度。

    Args:
        text: 原始文本
        max_len: 最大长度

    Returns:
        截断后的文本片段
    """
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ── CanonChecker ─────────────────────────────────────────────────────────


class CanonChecker:
    """世界观规则校验器。

    从 canon/ 目录解析规则，对文本进行轻量级规则违反检测。
    使用关键词匹配和肯定/否定语境检测，不依赖 LLM。

    使用方式:
        checker = CanonChecker()
        rules = checker.load_rules(canon_dir)
        violations = checker.check_text(text, rules)
    """

    def __init__(self) -> None:
        self._rules_cache: dict[str, list[CanonRule]] = {}
        """规则缓存，key=来源目录路径"""

    def load_rules(self, canon_dir: Path) -> list[CanonRule]:
        """从 canon 目录加载所有规则。

        支持缓存：同一目录多次加载返回同一实例列表。
        从 `## 规则` 章节的编号列表和关键陈述中提取规则。

        Args:
            canon_dir: canon 设定目录

        Returns:
            解析后的规则列表
        """
        dir_key = str(canon_dir.resolve())
        if dir_key in self._rules_cache:
            return self._rules_cache[dir_key]

        if not canon_dir.exists():
            logger.warning("Canon 目录不存在: %s", canon_dir)
            return []

        rules: list[CanonRule] = []
        md_files = sorted(canon_dir.glob("*.md"))
        if not md_files:
            logger.info("Canon 目录中无 Markdown 文件: %s", canon_dir)
            return []

        for md_file in md_files:
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                logger.error("读取 Canon 文件失败 %s: %s", md_file, e)
                continue

            file_rules = self._parse_rules(content, md_file.name)
            rules.extend(file_rules)

        self._rules_cache[dir_key] = rules
        logger.info(
            "从 %s 加载了 %d 条世界观规则（%d 个文件）",
            canon_dir,
            len(rules),
            len(md_files),
        )
        return rules

    def clear_cache(self) -> None:
        """清除规则缓存。"""
        self._rules_cache.clear()

    def _parse_rules(self, content: str, source_file: str) -> list[CanonRule]:
        """从 Markdown 内容中解析规则。

        从 `## 规则` 章节的编号列表中提取规则。
        同时从其他章节的关键陈述（bullet point）中提取隐含规则。

        Args:
            content: Markdown 文件内容
            source_file: 来源文件名

        Returns:
            规则列表
        """
        rules: list[CanonRule] = []

        # 提取 ## 规则 章节
        rule_section_match = _RULE_SECTION_RE.search(content)
        if rule_section_match:
            rule_text = rule_section_match.group(1)
            # 提取 numbered items
            for m in _NUMBERED_ITEM_RE.finditer(rule_text):
                item_text = m.group(1).strip()
                rule = self._create_rule(item_text, source_file)
                if rule:
                    rules.append(rule)

        # 从其他章节提取关键约束性陈述
        all_bullets = _BULLET_ITEM_RE.findall(content)
        for bullet in all_bullets:
            bullet = bullet.strip()
            # 只提取包含约束性词汇且不重复的陈述
            if (
                any(w in bullet for w in ["不能", "没有", "只能", "必须", "禁止", "不得"])
                and not any(r.raw_text == bullet for r in rules)
            ):
                rule = self._create_rule(bullet, source_file)
                if rule:
                    rules.append(rule)

        return rules

    def _create_rule(self, text: str, source_file: str) -> CanonRule | None:
        """从一行规则文本创建 CanonRule。

        Args:
            text: 规则文本
            source_file: 来源文件名

        Returns:
            CanonRule 实例，解析失败返回 None
        """
        # 移除尾部的注释/解释（——或—后的内容）
        constraint = re.split(r"[—\-]{2,}", text)[0].strip()
        if not constraint:
            return None

        rule_type = _get_rule_type(constraint)
        concept = _extract_core_concept(constraint)

        # 提取关键词：从完整约束文本中
        all_keywords = _extract_keywords(constraint)
        constraint_keywords = _extract_keywords(constraint)

        # 去重
        seen: set[str] = set()
        unique_keywords: list[str] = []
        for kw in all_keywords:
            kw_lower = kw.lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique_keywords.append(kw)

        return CanonRule(
            concept=concept,
            constraint=constraint,
            rule_type=rule_type,
            source_file=source_file,
            keywords=unique_keywords,
            constraint_keywords=list(set(kw.lower() for kw in constraint_keywords)),
            raw_text=text,
        )

    def check_text(
        self,
        text: str,
        rules: list[CanonRule],
    ) -> list[CanonViolation]:
        """检查文本是否违反世界观规则。

        策略:
        1. 按句分割文本
        2. 对每条规则，检查文本是否提到相关概念
        3. 根据规则类型执行针对性检查：
           - negation: 检查是否在肯定语境中提到被否定事物
           - exclusive: 检查是否由非许可者执行
           - positive: 检查是否缺少约束表达
        4. 对未明确违反但概念已出现的，给出 suggestion 级别提醒

        Args:
            text: 待检查的文本
            rules: 世界观规则列表

        Returns:
            违反规则列表，按严重程度降序排列
        """
        if not text.strip() or not rules:
            return []

        # 按句分割
        sentences = re.split(r"[。！？\n]+", text)

        violations: list[CanonViolation] = []
        seen_concepts: set[str] = set()

        for rule in rules:
            if rule.concept in seen_concepts:
                continue

            # 检测
            type_violations: list[CanonViolation] = []

            if rule.rule_type == "negation":
                type_violations = _check_negation_violation(rule, sentences)
            elif rule.rule_type == "exclusive":
                type_violations = _check_exclusive_violation(rule, sentences)

            if type_violations:
                violations.extend(type_violations)
                seen_concepts.add(rule.concept)
            else:
                # 未明确违反，检查是否缺少约束提及
                suggestions = _check_missing_constraint(rule, sentences)
                if suggestions:
                    violations.extend(suggestions)
                    seen_concepts.add(rule.concept)

        # 按严重程度降序排列
        severity_order = {"violation": 0, "warning": 1, "suggestion": 2}
        violations.sort(key=lambda v: severity_order.get(v.severity, 9))
        return violations

    def _has_inline_exemption(self, text: str, rule_concept: str) -> bool:
        """检测文本中是否包含行内豁免标记。

        支持格式: <!-- canon_exempt: rule_concept -->
        例如: "宝剑发出耀眼的光芒。<!-- canon_exempt: magic_no_glow -->"

        Args:
            text: 待检查的文本
            rule_concept: 规则概念名称

        Returns:
            True 表示存在豁免标记
        """
        pattern = re.escape(rule_concept)
        return bool(re.search(
            rf"<!-- canon_exempt:\s*{pattern}\s*-->",
            text,
        ))

    def check_text_with_exemptions(
        self,
        text: str,
        rules: list[CanonRule],
        chapter_exemptions: list[str] | None = None,
    ) -> list[CanonViolation]:
        """检查文本是否违反世界观规则（支持豁免标记）。

        在标准 check_text 基础上增加两层豁免：
        - A1: 行内豁免（Markdown 注释 `<!-- canon_exempt: concept -->`）
        - A2: 章节 Frontmatter 声明的豁免列表

        Args:
            text: 待检查的文本
            rules: 世界观规则列表
            chapter_exemptions: 章节级豁免的规则概念列表（可选）

        Returns:
            过滤后的违反规则列表
        """
        violations = self.check_text(text, rules)
        if not violations:
            return []

        # A1: 行内豁免
        filtered = []
        for v in violations:
            if self._has_inline_exemption(text, v.rule):
                continue
            filtered.append(v)

        # A2: 章节 Frontmatter 豁免
        if chapter_exemptions:
            exempt_set = {e.lower() for e in chapter_exemptions}
            filtered = [v for v in filtered if v.rule.lower() not in exempt_set]

        return filtered


# ── 便捷函数 ─────────────────────────────────────────────────────────────


def check_text_against_canon(
    text: str,
    canon_dir: Path,
    checker: CanonChecker | None = None,
) -> list[CanonViolation]:
    """便捷函数：一键检查文本与世界观规则。

    Args:
        text: 待检查的文本
        canon_dir: canon 设定目录
        checker: 可复用的 CanonChecker 实例

    Returns:
        违反规则列表
    """
    if checker is None:
        checker = CanonChecker()
    rules = checker.load_rules(canon_dir)
    return checker.check_text(text, rules)
