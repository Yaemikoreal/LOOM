"""GlobalSettingsDialog — 全局设置对话框。

菜单栏「文件 → 偏好设置」触发。
管理 API Key、默认模型、工作区目录等跨项目配置。
内置「测试连接」功能验证 LLM 可用性。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from opennovel_desktop.widgets.toast_notification import ToastNotification


class _ConnectionTestWorker(QObject):
    """后台 LLM 连接测试 Worker，在独立线程中运行。"""

    finished = Signal(bool, str)  # success, message

    def __init__(self, model: str, api_key: str, api_base: str) -> None:
        super().__init__()
        self._model = model
        self._api_key = api_key
        self._api_base = api_base

    @Slot()
    def run(self) -> None:
        """执行测试调用。"""
        try:
            import litellm

            litellm.set_verbose = False

            response = litellm.completion(
                model=self._model,
                api_key=self._api_key or None,
                api_base=self._api_base or None,
                messages=[{"role": "user", "content": "Reply with just 'OK'."}],
                max_tokens=5,
                timeout=10,
            )
            # 提取实际使用的模型名
            model_used = self._model
            if hasattr(response, "model") and response.model:
                model_used = response.model
            elif isinstance(response, dict) and response.get("model"):
                model_used = response["model"]

            self.finished.emit(True, f"连接成功！模型: {model_used}")
        except Exception as e:
            msg = str(e)
            # 缩短常见错误消息
            for keyword in ("API key", "Authentication", "auth", "key"):
                if keyword.lower() in msg.lower():
                    msg = f"API Key 无效或未设置: {msg}"
                    break
            self.finished.emit(False, f"连接失败: {msg}")


class GlobalSettingsDialog(QDialog):
    """全局设置模态对话框。"""

    # 测试连接结果信号（供主窗口 API 状态灯联动）
    connection_tested = Signal(bool, str)  # success, model_or_error

    def __init__(self, parent: QDialog | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("全局偏好设置")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = QLabel("全局设置")
        title.setStyleSheet("font-weight: 600; font-size: 16px;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)

        # 默认模型
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItems(
            [
                "deepseek/deepseek-v4-flash",
                "deepseek/deepseek-chat",
                "gpt-4",
                "gpt-4o",
                "claude-sonnet-4-6",
            ]
        )
        form.addRow("默认模型:", self._model_combo)

        # API Key
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-...")
        form.addRow("API Key:", self._api_key)

        # API Base
        self._api_base = QLineEdit()
        self._api_base.setPlaceholderText("https://api.deepseek.com")
        form.addRow("API Base:", self._api_base)

        # 工作区目录
        ws_row = QHBoxLayout()
        self._workspace = QLineEdit()
        self._workspace.setPlaceholderText(str(Path.home() / "OpenNovel" / "novels"))
        ws_row.addWidget(self._workspace, 1)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._browse)
        ws_row.addWidget(browse_btn)
        form.addRow("工作区:", ws_row)

        # 测试结果标签（内联显示，不被模态对话框遮挡）
        self._result_label = QLabel("")
        self._result_label.setStyleSheet("font-size: 12px; padding: 2px 0;")
        self._result_label.setWordWrap(True)
        form.addRow("", self._result_label)

        layout.addLayout(form)
        layout.addStretch()

        # 按钮行
        btn_row = QHBoxLayout()

        self._test_btn = QPushButton("测试连接")
        self._test_btn.setObjectName("SecondaryButton")
        self._test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self._test_btn)

        btn_row.addStretch()

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setObjectName("SecondaryButton")
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._load_config()
        self._test_thread: QThread | None = None
        self._test_worker: _ConnectionTestWorker | None = None
        self._closing: bool = False

    def _load_config(self) -> None:
        """从 .opennovel.yaml 加载当前配置。"""
        config_paths = [
            Path.cwd() / ".opennovel.yaml",
            Path.home() / ".opennovel.yaml",
        ]
        for path in config_paths:
            if path.exists():
                try:
                    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    if "default_model" in cfg:
                        self._model_combo.setCurrentText(cfg["default_model"])
                    if "default_api_key" in cfg:
                        self._api_key.setText(cfg["default_api_key"])
                    if "default_api_base" in cfg:
                        self._api_base.setText(cfg["default_api_base"])
                    if "workspace_dir" in cfg:
                        self._workspace.setText(str(cfg["workspace_dir"]))
                except Exception:
                    pass

    def _test_connection(self) -> None:
        """测试 LLM 连接（后台线程运行）。"""
        self._test_btn.setEnabled(False)
        self._test_btn.setText("测试中...")
        self._result_label.setText("")  # 清空上次结果
        QApplication.processEvents()

        model = self._model_combo.currentText()
        api_key = self._api_key.text().strip()
        api_base = self._api_base.text().strip()

        self._test_thread = QThread()
        self._test_worker = _ConnectionTestWorker(model, api_key, api_base)
        self._test_worker.moveToThread(self._test_thread)

        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.finished.connect(self._on_test_finished)
        self._test_worker.finished.connect(self._test_thread.quit)
        self._test_thread.finished.connect(self._cleanup_test_thread)

        self._test_thread.start()

    def closeEvent(self, event: object) -> None:  # noqa: N802
        """对话框关闭时终止测试线程。"""
        self._closing = True
        if self._test_thread and self._test_thread.isRunning():
            self._test_thread.quit()
            self._test_thread.wait(2000)
        super().closeEvent(event)

    def _on_test_finished(self, success: bool, message: str) -> None:
        """测试完成回调（对话框已关闭时跳过）。"""
        if self._closing:
            return
        self._test_btn.setEnabled(True)
        self._test_btn.setText("测试连接")

        # 内联显示结果（模态对话框遮挡 ToastNotification，用标签代替）
        if success:
            self._result_label.setStyleSheet("color: #4A7C5B; font-size: 12px;")
            self._result_label.setText(f"✓ {message}")
        else:
            self._result_label.setStyleSheet("color: #B85C4A; font-size: 12px;")
            self._result_label.setText(f"✗ {message}")

        # 广播测试结果（主窗口 API 状态灯联动）
        self.connection_tested.emit(success, message)

    def _cleanup_test_thread(self) -> None:
        """清理测试线程。"""
        if self._test_thread:
            self._test_thread.deleteLater()
            self._test_thread = None
        if self._test_worker:
            self._test_worker.deleteLater()
            self._test_worker = None

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        dir_path = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if dir_path:
            self._workspace.setText(dir_path)

    def _save(self) -> None:
        """保存配置到 .opennovel.yaml。"""
        config = {}
        model = self._model_combo.currentText()
        if model:
            config["default_model"] = model
        api_key = self._api_key.text().strip()
        if api_key:
            config["default_api_key"] = api_key
        api_base = self._api_base.text().strip()
        if api_base:
            config["default_api_base"] = api_base
        workspace = self._workspace.text().strip()
        if workspace:
            config["workspace_dir"] = workspace

        paths = [
            Path.cwd() / ".opennovel.yaml",
            Path.home() / ".opennovel.yaml",
        ]
        saved = False
        for path in paths:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                saved = True
            except OSError:
                continue

        if saved:
            ToastNotification.show_success("全局设置已保存")
            self.accept()
        else:
            ToastNotification.show_error("保存失败：无法写入配置文件", persistent=True)
