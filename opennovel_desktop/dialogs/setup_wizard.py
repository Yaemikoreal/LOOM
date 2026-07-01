"""SetupWizard — 首次运行设置向导。

4 步 QWizard：欢迎 → 工作区目录 → API Key → 默认模型。
完成后生成 .opennovel.yaml 全局配置文件。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)


class _WelcomePage(QWizardPage):
    """第 1 步：欢迎。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("欢迎使用 OpenNovel Desktop")
        self.setSubTitle("")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        welcome = QLabel(
            "OpenNovel 是一款本地优先的长篇小说叙事操作系统。\n\n"
            "本向导将帮助您完成首次配置：\n"
            "  • 选择小说工作区目录\n"
            "  • 配置 AI 模型 API Key\n"
            "  • 选择默认创作模型\n\n"
            "所有配置均可之后在「偏好设置」中修改。"
        )
        welcome.setWordWrap(True)
        welcome.setStyleSheet("font-size: 14px; line-height: 1.6; padding: 12px;")
        layout.addWidget(welcome)


class _WorkspacePage(QWizardPage):
    """第 2 步：工作区目录。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("选择工作区目录")
        self.setSubTitle("所有小说项目将存放在此目录下")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        desc = QLabel(
            "工作区是存放所有小说项目的父目录。\n每个项目 init 时在此目录下创建子文件夹。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 13px;")
        layout.addWidget(desc)

        path_layout = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("选择或输入工作区路径")
        default = str(Path.home() / "OpenNovel" / "novels")
        self._path_edit.setText(default)
        path_layout.addWidget(self._path_edit, 1)

        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse)
        path_layout.addWidget(browse_btn)

        layout.addLayout(path_layout)

    def _browse(self) -> None:
        """打开目录选择对话框。"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if dir_path:
            self._path_edit.setText(dir_path)

    def workspace_path(self) -> str:
        """返回选择的工作区路径。"""
        return self._path_edit.text().strip()


class _ApiKeyPage(QWizardPage):
    """第 3 步：API Key 配置。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("配置 API Key")
        self.setSubTitle("选择一个 AI 提供商并输入 API Key")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        desc = QLabel(
            "OpenNovel 通过 LiteLLM 支持多种 AI 模型提供商。\n选择您使用的服务商并输入 API Key。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 13px;")
        layout.addWidget(desc)

        form = QFormLayout()

        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["DeepSeek", "OpenAI", "自定义"])
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        form.addRow("提供商:", self._provider_combo)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("sk-...")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key:", self._api_key_edit)

        self._api_base_edit = QLineEdit()
        self._api_base_edit.setPlaceholderText("可选：自定义 API 端点 URL")
        form.addRow("API Base (可选):", self._api_base_edit)

        layout.addLayout(form)

        hint = QLabel("API Key 仅保存在本地 .opennovel.yaml 中，不会上传。")
        hint.setStyleSheet("color: #A8A49E; font-size: 11px;")
        layout.addWidget(hint)

    def _on_provider_changed(self, provider: str) -> None:
        """提供商切换时更新占位符。"""
        placeholders = {
            "DeepSeek": "sk-...",
            "OpenAI": "sk-...",
            "自定义": "API Key",
        }
        self._api_key_edit.setPlaceholderText(placeholders.get(provider, "API Key"))
        if provider == "DeepSeek":
            self._api_base_edit.setPlaceholder("https://api.deepseek.com")
        elif provider == "OpenAI":
            self._api_base_edit.setPlaceholder("https://api.openai.com/v1")
        else:
            self._api_base_edit.setPlaceholder("https://")

    def provider(self) -> str:
        """返回选择的提供商名称。"""
        return self._provider_combo.currentText()

    def api_key(self) -> str:
        """返回输入的 API Key。"""
        return self._api_key_edit.text().strip()

    def api_base(self) -> str:
        """返回输入的 API Base URL。"""
        return self._api_base_edit.text().strip()


class _ModelPage(QWizardPage):
    """第 4 步：默认模型选择。"""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("选择默认模型")
        self.setSubTitle("设置全局默认 AI 创作模型")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        desc = QLabel("选择生成小说内容的 AI 模型。\n每个项目可在 novel.yaml 中单独覆盖此设置。")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 13px;")
        layout.addWidget(desc)

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
        self._model_combo.setCurrentText("deepseek/deepseek-v4-flash")
        layout.addWidget(self._model_combo)

        tip = QLabel("提示：deekseek-v4-flash 是目前推荐的高性价比模型。")
        tip.setStyleSheet("color: #A8A49E; font-size: 11px;")
        layout.addWidget(tip)

    def model(self) -> str:
        """返回选择的模型名称。"""
        return self._model_combo.currentText()


class SetupWizard(QWizard):
    """首次运行设置向导（4 步）。"""

    def __init__(self, parent: QWizard | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OpenNovel 首次设置")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(560, 420)

        self._welcome_page = _WelcomePage()
        self._workspace_page = _WorkspacePage()
        self._apikey_page = _ApiKeyPage()
        self._model_page = _ModelPage()

        self.addPage(self._welcome_page)
        self.addPage(self._workspace_page)
        self.addPage(self._apikey_page)
        self.addPage(self._model_page)

    def apply_config(self) -> None:
        """将向导配置写入 .opennovel.yaml。"""
        import yaml  # noqa: PLC0415

        workspace = self._workspace_page.workspace_path()
        api_key = self._apikey_page.api_key()
        api_base = self._apikey_page.api_base()
        model = self._model_page.model()

        config = {
            "default_model": model,
            "workspace_dir": workspace,
        }
        if api_key:
            config["default_api_key"] = api_key
        if api_base:
            config["default_api_base"] = api_base

        # 写入到用户目录和项目根
        paths = [
            Path.home() / ".opennovel.yaml",
            Path.cwd() / ".opennovel.yaml",
        ]
        for path in paths:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            except OSError:
                continue
