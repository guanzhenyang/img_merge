"""本地启动图片工坊。"""

from __future__ import annotations

import sys


def _check_runtime() -> str | None:
    if sys.version_info < (3, 11):
        return "需要 Python 3.11 或更高版本。"
    try:
        import tkinter  # noqa: F401
    except Exception:
        return "未找到 tkinter。安装 Python 时请勾选 tcl/tk。"
    missing = []
    for name in ("customtkinter", "PIL"):
        try:
            __import__(name)
        except Exception:
            missing.append(name)
    if missing:
        return "缺少依赖：" + "、".join(missing) + "。请运行 python -m pip install -r requirements.txt"
    return None


def main() -> None:
    problem = _check_runtime()
    if problem:
        sys.stderr.write(problem + "\n")
        raise SystemExit(1)
    from app.main import main as run_app

    run_app()


if __name__ == "__main__":
    main()
