"""SettingsPanel — 项目设置内联面板。

高频字段表单（模型/temperature/Token/创作方向）+ YAML 逃生舱。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from opennovel_desktop.app_state import AppState


class SettingsPanel(QWidget):
    """项目设置面板。"""

    def __init__(self) -> None:
        super().__init__()
        self._project_root: str = ""
        self._yaml_visible: bool = False

        self._setup_ui()

        try:
            state = AppState.instance()
            state.project_changed.connect(self._on_project_changed)
        except (AssertionError, RuntimeError):
            pass

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        title = QLabel("项目设置")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setSpacing(10)

        # 模型选择
        form_layout.addWidget(QLabel("模型"))
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItems(
            [
                "deepseek/deepseek-v4-flash",
                "deepseek/deepseek-chat",
                "deepseek/deepseek-r1",
                "gpt-4",
                "gpt-4o",
                "claude-sonnet-4-6",
            ]
        )
        form_layout.addWidget(self._model_combo)

        # Temperature
        form_layout.addWidget(QLabel("Temperature"))
        temp_row = QHBoxLayout()
        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        self._temp_slider.setRange(0, 100)
        self._temp_slider.setValue(70)
        self._temp_label = QLabel("0.70")
        self._temp_label.setFixedWidth(40)
        self._temp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._temp_slider.valueChanged.connect(lambda v: self._temp_label.setText(f"{v / 100:.2f}"))
        temp_row.addWidget(self._temp_slider, 1)
        temp_row.addWidget(self._temp_label)
        form_layout.addLayout(temp_row)

        # Token 上限
        form_layout.addWidget(QLabel("Token 上限"))
        self._token_spin = QSpinBox()
        self._token_spin.setRange(1000, 32000)
        self._token_spin.setValue(8000)
        self._token_spin.setSingleStep(1000)
        form_layout.addWidget(self._token_spin)

        # 创作方向
        form_layout.addWidget(QLabel("创作方向"))
        self._direction_edit = QPlainTextEdit()
        self._direction_edit.setPlaceholderText("例如：黑暗奇幻，克苏鲁元素")
        self._direction_edit.setMaximumHeight(80)
        form_layout.addWidget(self._direction_edit)

        form_layout.addStretch()
        scroll.setWidget(form)
        layout.addWidget(scroll, 1)

        # 保存按钮
        self._save_btn = QPushButton("保存设置")
        self._save_btn.clicked.connect(self._save_settings)
        layout.addWidget(self._save_btn)

        # YAML 逃生舱
        self._yaml_btn = QPushButton("编辑原始 YAML ▼")
        self._yaml_btn.setObjectName("SecondaryButton")
        self._yaml_btn.clicked.connect(self._toggle_yaml)
        layout.addWidget(self._yaml_btn)

        self._yaml_editor = QPlainTextEdit()
        self._yaml_editor.setVisible(False)
        self._yaml_editor.setMaximumHeight(200)
        self._yaml_editor.setStyleSheet("font-family: JetBrains Mono; font-size: 12px;")
        layout.addWidget(self._yaml_editor)

    def _on_project_changed(self, project_root: str) -> None:
        """项目切换时加载配置。"""
        self._project_root = project_root
        self._load_config()

    def _load_config(self) -> None:
        """加载 novel.yaml 配置到表单。"""
        if not self._project_root:
            return
        try:
            from opennovel.core.config import LoomConfig  # noqa: PLC0415

            config = LoomConfig.load(Path(self._project_root))
            self._model_combo.setCurrentText(config.model)
            self._temp_slider.setValue(int(config.extra.get("temperature", 0.7) * 100))
            self._token_spin.setValue(config.token_budget)
            self._direction_edit.setPlainText(config.creative_direction)
        except (OSError, ValueError, ImportError):
            pass

    def _save_settings(self) -> None:
        """保存表单设置到 novel.yaml。"""
        if not self._project_root:
            return
        try:
            from opennovel.core.config import LoomConfig  # noqa: PLC0415

            config = LoomConfig.load(Path(self._project_root))
            config.model = self._model_combo.currentText()
            config.token_budget = self._token_spin.value()
            config.creative_direction = self._direction_edit.toPlainText()
            if "temperature" not in config.extra:
                config.extra["temperature"] = 0.7
            config.extra["temperature"] = self._temp_slider.value() / 100
            config.save(Path(self._project_root))

            from opennovel_desktop.widgets.toast_notification import (
                ToastNotification,  # noqa: PLC0415
            )

            ToastNotification.show_success("设置已保存")
        except (OSError, ValueError, ImportError):
            pass

    def _toggle_yaml(self) -> None:
        """展开/折叠 YAML 编辑器。"""
        self._yaml_visible = not self._yaml_visible
        self._yaml_editor.setVisible(self._yaml_visible)
        self._yaml_btn.setText("编辑原始 YAML ▼" if not self._yaml_visible else "编辑原始 YAML ▲")

        if self._yaml_visible and self._project_root:
            try:
                yaml_path = Path(self._project_root) / "novel.yaml"
                if yaml_path.exists():
                    self._yaml_editor.setPlainText(yaml_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                pass
