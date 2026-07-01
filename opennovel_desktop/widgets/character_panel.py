"""CharacterPanel — 角色卡片面板。

左侧角色名列表（QListWidget），右侧选中角色详情（QTextBrowser）。
底部「发送给 Agent」快捷按钮。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from opennovel_desktop.app_state import AppState


class CharacterPanel(QWidget):
    """角色卡片面板。"""

    send_to_agent = Signal(str)  # character_id
    open_character_file = Signal(str)  # file_path

    def __init__(self, app_state: AppState | None = None) -> None:
        super().__init__()
        self._app_state = app_state or AppState.instance()
        self._project_root: str = ""
        self._characters: dict[str, str] = {}  # char_id → file_path
        self._current_char_id: str = ""

        self._setup_ui()
        self._app_state.project_changed.connect(self._on_project_changed)

    def _setup_ui(self) -> None:
        """构建左右分栏布局。"""
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：角色列表
        self._list = QListWidget()
        self._list.setMinimumWidth(100)
        self._list.currentRowChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._list)

        # 右侧：详情视图
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)

        self._detail = QTextBrowser()
        self._detail.setOpenExternalLinks(False)
        content.addWidget(self._detail, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(8, 8, 8, 8)

        self._send_btn = QPushButton("发送给 Agent")
        self._send_btn.setObjectName("SecondaryButton")
        self._send_btn.setEnabled(False)
        self._send_btn.clicked.connect(self._on_send_to_agent)
        btn_layout.addWidget(self._send_btn)

        self._open_btn = QPushButton("打开文件")
        self._open_btn.setObjectName("SecondaryButton")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open_file)
        btn_layout.addWidget(self._open_btn)

        content.addLayout(btn_layout)

        detail_container = QWidget()
        detail_container.setLayout(content)
        splitter.addWidget(detail_container)

        # 默认布局比例
        splitter.setSizes([120, 280])

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(splitter)

    def _on_project_changed(self, project_root: str) -> None:
        """项目切换时重新加载角色列表。"""
        self._project_root = project_root
        self._load_characters()

    def _load_characters(self) -> None:
        """从 characters/ 目录加载角色文件列表。"""
        self._list.clear()
        self._characters.clear()
        self._detail.clear()
        self._send_btn.setEnabled(False)

        if not self._project_root:
            return

        chars_dir = Path(self._project_root) / "characters"
        if not chars_dir.exists():
            return

        for file_path in sorted(chars_dir.glob("*.md")):
            char_id = file_path.stem  # "char_001"
            self._characters[char_id] = str(file_path)

            # 尝试从 Frontmatter 读取角色名
            display_name = char_id
            try:
                content = file_path.read_text(encoding="utf-8")
                # 简单提取 YAML 中的 name 字段
                import re

                match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
                if match:
                    display_name = f"{match.group(1).strip()} ({char_id})"
            except Exception:
                pass

            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, char_id)
            self._list.addItem(item)

    def _on_selection_changed(self, row: int) -> None:
        """角色选择变更时更新详情。"""
        if row < 0:
            self._detail.clear()
            self._send_btn.setEnabled(False)
            self._open_btn.setEnabled(False)
            return

        item = self._list.item(row)
        if item is None:
            return

        char_id = item.data(Qt.ItemDataRole.UserRole)
        file_path = self._characters.get(char_id)
        if not file_path:
            return

        self._current_char_id = char_id
        self._send_btn.setEnabled(True)
        self._open_btn.setEnabled(True)

        # 读取并渲染 Markdown 内容
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            # 简单 Markdown 渲染
            html = self._md_to_html(content)
            self._detail.setHtml(html)
        except OSError:
            self._detail.setPlainText("无法读取角色文件")

    def _on_send_to_agent(self) -> None:
        """发射发送给 Agent 信号。"""
        if self._current_char_id:
            self.send_to_agent.emit(self._current_char_id)

    def _on_open_file(self) -> None:
        """在编辑器中打开当前角色文件。"""
        file_path = self._characters.get(self._current_char_id)
        if file_path:
            self.open_character_file.emit(file_path)

    @staticmethod
    def _md_to_html(md: str) -> str:
        """极简 Markdown → HTML 转换（用于详情渲染）。"""
        import html

        lines = md.split("\n")
        html_parts: list[str] = []
        in_fm = False
        fm_html = ""

        for line in lines:
            # YAML Frontmatter 过滤
            if line.strip() == "---":
                in_fm = not in_fm
                if not in_fm:
                    html_parts.append(
                        f'<div style="color: #A8A49E; font-size: 11px; '
                        f'font-family: monospace; margin-bottom: 16px;">'
                        f"{fm_html}</div>"
                    )
                    fm_html = ""
                continue
            if in_fm:
                fm_html += f"{html.escape(line)}<br>"
                continue

            stripped = line.strip()
            if not stripped:
                html_parts.append("<br>")
            elif stripped.startswith("### "):
                html_parts.append(f"<h3>{html.escape(stripped[4:])}</h3>")
            elif stripped.startswith("## "):
                html_parts.append(f"<h2>{html.escape(stripped[3:])}</h2>")
            elif stripped.startswith("# "):
                html_parts.append(f"<h1>{html.escape(stripped[2:])}</h1>")
            elif stripped.startswith("**") and stripped.endswith("**"):
                html_parts.append(f"<p><b>{html.escape(stripped[2:-2])}</b></p>")
            elif stripped.startswith("- "):
                html_parts.append(f"<li>{html.escape(stripped[2:])}</li>")
            else:
                html_parts.append(f"<p>{html.escape(stripped)}</p>")

        if in_fm:
            return "<p>无法解析角色文件</p>"
        return "".join(html_parts)
