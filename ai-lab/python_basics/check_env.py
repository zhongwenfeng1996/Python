#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python 学习环境自检

在开始 python_basics 的练习之前（或遇到环境问题时）跑一次：

    cd ai-lab/python_basics
    python3 check_env.py

它会检查 Python 版本、必需文件、环境变量、mock 服务状态、虚拟环境。
零依赖，只用标准库。
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

OK = "\033[32m✅\033[0m"
BAD = "\033[31m❌\033[0m"
WARN = "\033[33m⚠️ \033[0m"

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "", fix: str = "", warn_only: bool = False) -> bool:
    mark = OK if ok else (WARN if warn_only else BAD)
    line = f"{mark} {label}"
    if detail:
        line += f"   {detail}"
    print(line)
    if not ok and fix:
        print(f"     → {fix}")
    if not warn_only:
        results.append(ok)
    return ok


def main() -> int:
    here = Path(__file__).resolve().parent
    print("=" * 60)
    print("  Python 学习环境自检")
    print("=" * 60)
    print()

    # ---------- 1. Python 版本 ----------
    v = sys.version_info
    check(
        f"Python 版本  {v.major}.{v.minor}.{v.micro}",
        v >= (3, 9),
        fix="需要 Python 3.9 或更高（推荐 3.11+）",
    )
    print(f"     解释器路径：{sys.executable}")

    # ---------- 2. 文件完整性 ----------
    print()
    print("—— 文件检查 ——")
    required = {
        "01_hello_llm.py": "第一个练习脚本",
        "02_stream_chat.py": "流式对话脚本",
        "03_chunk_text.py": "文本切片脚本",
        "js_to_python.md": "JS → Python 速查表",
        "docs/README.md": "教程目录",
        "docs/01-变量与类型.md": "第 1 章",
        "docs/10-调试与报错.md": "第 10 章",
    }
    missing = []
    for name, desc in required.items():
        exists = (here / name).exists()
        if not exists:
            missing.append(name)
        check(f"{name:<26} {desc}", exists, fix=f"文件缺失：{here / name}")

    mock = here.parent / "week01" / "mock_server.py"
    check(f"{'../week01/mock_server.py':<26} 本地 mock 服务", mock.exists(),
          fix="mock 服务文件缺失，01/02 脚本无法离线测试")

    # ---------- 3. 目录位置 ----------
    print()
    print("—— 目录检查 ——")
    check("当前目录是否正确", here.name == "python_basics",
          detail=f"{here}",
          fix="请先 cd 到 ai-lab/python_basics 再运行")

    # ---------- 4. 环境变量 ----------
    print()
    print("—— 配置检查（可选）——")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("MODEL", "")

    if api_key and api_key != "test":
        check("OPENAI_API_KEY 已设置", True, detail=f"{api_key[:6]}...（已隐藏）")
    elif api_key == "test":
        check("OPENAI_API_KEY", True, detail="test（mock 模式）", warn_only=True)
    else:
        check("OPENAI_API_KEY 未设置", False,
              detail="→ 只能用 mock 服务练习，不能真实调用",
              fix="export OPENAI_API_KEY=sk-你的key", warn_only=True)

    check("OPENAI_BASE_URL", bool(base_url), detail=base_url or "（用默认值）", warn_only=True)
    check("MODEL", bool(model), detail=model or "（用默认值）", warn_only=True)

    # ---------- 5. mock 服务是否在跑 ----------
    print()
    print("—— 服务状态 ——")

    def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0

    mock_running = port_open("127.0.0.1", 8765)
    check(
        "mock 服务 (127.0.0.1:8765)",
        mock_running,
        detail="正在运行" if mock_running else "未启动",
        fix="另开一个终端运行：python3 ../week01/mock_server.py",
        warn_only=True,
    )

    # ---------- 6. 虚拟环境 ----------
    print()
    print("—— 环境隔离 ——")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    venv_dir = here.parent / ".venv"
    check(
        "虚拟环境",
        in_venv,
        detail="已激活" if in_venv else ("存在但未激活" if venv_dir.exists() else "未创建"),
        fix="python3 -m venv .venv && source .venv/bin/activate"
            "（前 3 天练习零依赖，可以不建）",
        warn_only=True,
    )

    # ---------- 结论 ----------
    print()
    print("=" * 60)
    failed = results.count(False)
    if failed == 0:
        print(f"{OK} 环境检查通过，可以开始练习了。")
        print()
        print("下一步：")
        print("  终端 1： python3 ../week01/mock_server.py")
        print("  终端 2： OPENAI_BASE_URL=http://127.0.0.1:8765/v1 \\")
        print("           OPENAI_API_KEY=test python3 01_hello_llm.py")
    else:
        print(f"{BAD} 有 {failed} 项必需检查未通过，请先按上面的提示修复。")
        if missing:
            print(f"   缺失文件：{', '.join(missing)}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
