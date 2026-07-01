"""MarkdownHighlighter — 基于 QSyntaxHighlighter 的轻量 Markdown 语法高亮。

匹配规则按优先级降序排列，确保高优先级规则不被低优先级覆盖。
色值通过 ThemeLoader.current_colors() 获取，随主题切换自动更新。
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from opennovel_desktop.app_state import AppState
from opennovel_desktop.styles.theme_loader import ThemeLoader


def _make_format(
    color: str,
    bold: bool = False,
    italic: bool = False,
    font_family: str | None = None,
    underline: bool = False,
) -> QTextCharFormat:
    """从色值字符串创建 QTextCharFormat。"""
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    if font_family:
        fmt.setFontFamilies([font_family])
    if underline:
        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
    return fmt


class MarkdownHighlighter(QSyntaxHighlighter):
    """轻量 Markdown 语法高亮器。

    匹配 8 种 Token 类型：标题 H1/H2、标题 H3+、加粗、斜体、
    对话引用、分割线、YAML Frontmatter、行内代码、链接。
    """

    def __init__(self, parent: QTextDocument | None = None) -> None:
        super().__init__(parent)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._multiline_rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._build_rules()

        # 监听主题切换，重建着色规则
        try:
            state = AppState.instance()
            state.theme_changed.connect(self._on_theme_changed)
        except (AssertionError, RuntimeError):
            pass

    # ── 公开接口 ──────────────────────────────────────────

    def reload_colors(self) -> None:
        """从当前主题重新加载色值并重建规则。"""
        self._build_rules()
        self.rehighlight()

    # ── 规则构建 ──────────────────────────────────────────

    def _build_rules(self) -> None:
        """按优先级降序构建正则匹配规则。"""
        colors = ThemeLoader.current_colors()
        self._rules.clear()
        self._multiline_rules.clear()

        # 1. YAML Frontmatter 块 (--- ... ---)，多行匹配
        yaml_fmt = _make_format(colors["yaml_fm"], italic=True)
        self._multiline_rules.append(
            (
                QRegularExpression(r"^---$"),
                QRegularExpression(r"^---$"),
                yaml_fmt,
            )
        )

        # 2. 标题 H1: # ...
        h1_fmt = _make_format(colors["heading1"], bold=True)
        self._rules.append((QRegularExpression(r"^#{1}\s+.*$"), h1_fmt))

        # 3. 标题 H2: ## ...
        h2_fmt = _make_format(colors["heading2"], bold=True)
        self._rules.append((QRegularExpression(r"^#{2}\s+.*$"), h2_fmt))

        # 4. 标题 H3+: ### ... up to ######
        h3_fmt = _make_format(colors["heading3"])
        self._rules.append((QRegularExpression(r"^#{3,6}\s+.*$"), h3_fmt))

        # 5. 分割线: --- (至少 3 个)
        hr_fmt = _make_format(colors["hr"])
        self._rules.append((QRegularExpression(r"^-{3,}$"), hr_fmt))

        # 6. 行内代码 `code`
        code_fmt = _make_format(colors["inline_code"], font_family="JetBrains Mono")
        self._rules.append((QRegularExpression(r"`[^`]+`"), code_fmt))

        # 7. 加粗 **text**
        bold_fmt = _make_format(colors["bold"], bold=True)
        self._rules.append((QRegularExpression(r"\*\*[^*]+\*\*"), bold_fmt))

        # 8. 斜体 *text*（不匹配加粗）
        italic_fmt = _make_format(colors["italic"], italic=True)
        self._rules.append((QRegularExpression(r"(?<!\*)\*[^*\s][^*]*[^*\s]\*(?!\*)"), italic_fmt))

        # 9. 对话引用 > ...
        quote_fmt = _make_format(colors["blockquote"], italic=True)
        self._rules.append((QRegularExpression(r"^>\s+.*$"), quote_fmt))

        # 10. 链接 [text](url)
        link_fmt = _make_format(colors["link"], underline=True)
        self._rules.append((QRegularExpression(r"\[([^\]]+)\]\(([^)]+)\)"), link_fmt))

    # ── 核心 virtual ──────────────────────────────────────

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        """对单行文本应用语法高亮。"""
        # 先检查多行规则（YAML Frontmatter）
        for start_pattern, end_pattern, fmt in self._multiline_rules:
            self._highlight_multiline(text, start_pattern, end_pattern, fmt)
            if self.currentBlockState() != -1:
                return  # 多行块内不再应用单行规则

        # 应用单行规则
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                start = match.capturedStart()
                length = match.capturedLength()
                self.setFormat(start, length, fmt)

    def _highlight_multiline(
        self,
        text: str,
        start_pattern: QRegularExpression,
        end_pattern: QRegularExpression,
        fmt: QTextCharFormat,
    ) -> None:
        """处理跨行块（如 YAML Frontmatter）。"""
        # 根据前一块状态决定是否在块内
        prev_state = self.previousBlockState()

        if prev_state == 1:
            # 在 YAML 块内，检查结束
            end_match = end_pattern.match(text)
            if end_match.hasMatch():
                self.setFormat(0, len(text), fmt)
                self.setCurrentBlockState(0)
            else:
                self.setFormat(0, len(text), fmt)
                self.setCurrentBlockState(1)
            return

        # 不在块内，检查开始
        start_match = start_pattern.match(text)
        if start_match.hasMatch():
            end_match = end_pattern.match(text, start_match.capturedEnd())
            if end_match.hasMatch():
                # 开始和结束在同一行
                self.setFormat(0, end_match.capturedEnd(), fmt)
                self.setCurrentBlockState(0)
            else:
                # 开始在本行，结束在后续行
                end_pos = len(text) - start_match.capturedStart()
                self.setFormat(start_match.capturedStart(), end_pos, fmt)
                self.setCurrentBlockState(1)
        else:
            self.setCurrentBlockState(0)

    # ── 主题切换 ──────────────────────────────────────────

    def _on_theme_changed(self, theme: str) -> None:  # noqa: ARG002
        """主题切换时重建高亮规则。"""
        self._build_rules()
        self.rehighlight()
