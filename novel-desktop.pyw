"""OpenNovel Desktop — Windows 双击启动入口。

Windows 的 .pyw 关联到 pythonw.exe（无控制台窗口），双击即开。
优于 .bat 的地方：没有控制台黑窗，启动更静默。
"""

import sys
import subprocess
from pathlib import Path


def main() -> None:
    """找到 .venv 的 python.exe 并启动 GUI。"""
    script_dir = Path(__file__).resolve().parent
    python_exe = script_dir / ".venv" / "Scripts" / "python.exe"
    pythonw_exe = script_dir / ".venv" / "Scripts" / "pythonw.exe"

    # 优先 pythonw（无窗），回退 python（有窗）
    launcher = pythonw_exe if pythonw_exe.exists() else python_exe

    if not launcher.exists():
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"未找到虚拟环境 Python:\n{launcher}\n\n"
            f"请先运行以下命令:\n"
            f"  py -3.11 -m venv .venv\n"
            f"  .venv\\Scripts\\python.exe -m pip install -e '.[gui]'",
            "OpenNovel Desktop — 启动失败",
            0x10,  # MB_ICONERROR
        )
        sys.exit(1)

    # 启动 GUI
    try:
        proc = subprocess.Popen(
            [str(launcher), "-m", "opennovel_desktop"],
            cwd=str(script_dir),
            creationflags=subprocess.CREATE_NO_WINDOW if launcher == pythonw_exe else 0,
        )
        # 如果用的是 pythonw，这里直接退出；python 则等待
        if launcher == python_exe:
            proc.wait()
    except FileNotFoundError:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"无法启动 Python:\n{launcher}",
            "OpenNovel Desktop — 启动失败",
            0x10,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
