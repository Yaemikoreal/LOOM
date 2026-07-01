# -*- mode: python ; coding: utf-8 -*-
#
# OpenNovel Desktop — PyInstaller 打包配置
# 构建命令:
#   pip install pyinstaller
#   pyinstaller novel-desktop.spec --clean
#
# 产物: dist/novel-desktop/novel-desktop.exe (单目录模式)
#       或 dist/novel-desktop.exe (单文件模式)
#

import sys
from pathlib import Path

# ── 项目路径 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
VENV_SITE = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"

# ── 数据文件 ──────────────────────────────────────────────
RESOURCES = [
    (str(PROJECT_ROOT / "opennovel_desktop" / "resources" / "icons"), "opennovel_desktop/resources/icons"),
    (str(PROJECT_ROOT / "opennovel_desktop" / "resources" / "themes"), "opennovel_desktop/resources/themes"),
]
# 如果编译了 QRC 则包含 .rcc 或 .py
QRC_FILE = PROJECT_ROOT / "opennovel_desktop" / "resources" / "resources_rc.py"
if QRC_FILE.exists():
    RESOURCES.append((str(QRC_FILE), "opennovel_desktop/resources"))

# ── 隐藏导入（PyInstaller 自动检测可能遗漏的模块）──────
HIDDEN_IMPORTS = [
    # PySide6 插件
    "PySide6.QtPlugin",
    "PySide6.QtSvg",
    # opennovel core 子模块
    "opennovel",
    "opennovel.core",
    "opennovel.core.llm",
    "opennovel.core.config",
    "opennovel.core.global_config",
    "opennovel.core.retriever",
    "opennovel.core.state_manager",
    "opennovel.core.context_assembler",
    "opennovel.core.hybrid_retriever",
    "opennovel.core.search_pipeline",
    "opennovel.agents",
    "opennovel.agents.writer",
    "opennovel.agents.critic",
    "opennovel.agents.manager",
    "opennovel.agents.director",
    "opennovel.agents.auditor",
    "opennovel.storage",
    "opennovel.storage.sqlite",
    "opennovel.storage.yaml_storage",
    "opennovel.schemas",
    "opennovel.prompts",
]

# ── a = Analysis ──────────────────────────────────────────
a = Analysis(
    [str(PROJECT_ROOT / "opennovel_desktop" / "__main__.py")],
    pathex=[str(PROJECT_ROOT), str(VENV_SITE)],
    binaries=[],
    datas=RESOURCES,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "notebook",
        "jupyter",
        "setuptools._distutils",
        "distutils",
        "PIL",
        "cv2",
    ],
    noarchive=False,
)

# ── pyz = PYZ ────────────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# ── exe = EXE ────────────────────────────────────────────
# 单目录模式（启动更快，调试方便）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="novel-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # True=开发带控制台, False=发布无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "opennovel_desktop" / "resources" / "app.ico")
    if (PROJECT_ROOT / "opennovel_desktop" / "resources" / "app.ico").exists()
    else None,
)

# ── COLLECT（单目录模式下收集所有文件）──────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="novel-desktop",
)
