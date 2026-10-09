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
    # 门槛是 3.10 而不是 3.9：教程第 08 章用了 `ToolCall | None` 这种写法，
    # 它在 3.9 上会直接 TypeError（Pydantic 会去求值注解）。
    # 自检放行 3.9、教程却跑不起来，是比"不检查"更糟的体验。
    v = sys.version_info
    check(
        f"Python 版本  {v.major}.{v.minor}.{v.micro}",
        v >= (3, 10),
        fix="需要 Python 3.10 或更高（推荐 3.12）——教程第 08 章用到 `X | None` 语法",
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
    }
    # 逐章检查教程文件：只抽查首尾两章的话，中间删掉几章也照样"通过"，
    # 那这个自检就没有意义了。
    #
    # ⚠️ 这张表必须与真实章节顺序同步 —— 重排章节（2026-10 把「函数」提到第 4 章）
    #    之后如果忘了改这里，自检会报"第 4 章缺失"，而其实只是改了名字。
    chapter_titles = {
        1: "变量与类型", 2: "字符串与格式化", 3: "列表字典与推导式",
        4: "函数", 5: "条件循环与作用域",
        6: "异常与文件", 7: "模块与虚拟环境",
        8: "类与对象", 9: "异步与并发", 10: "调试与报错",
    }
    for num, title in chapter_titles.items():
        required[f"docs/{num:02d}-{title}.md"] = f"第 {num} 章"
    # 附录也要查：章节里有多处"见附录 A"的指路，附录丢了那些链接就成了空指针
    required["docs/附-A-读懂一次模型API调用.md"] = "附录 A（读懂 API 响应）"

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
              fix='Windows: $env:OPENAI_API_KEY="sk-你的key"'
                  '  ｜  macOS/Linux: export OPENAI_API_KEY=sk-你的key',
              warn_only=True)

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
        fix="另开一个终端运行：py -3 ..\\week01\\mock_server.py（Windows）"
            " 或 python3 ../week01/mock_server.py（macOS/Linux）",
        warn_only=True,
    )

    # ---------- 6. 虚拟环境 ----------
    print()
    print("—— 环境隔离 ——")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    # 虚拟环境可能建在 python_basics 下（教程正文的写法），也可能建在 ai-lab 下（仓库根）。
    # 两处都检查，否则"照教程做了"却被报成"未创建"，会把人引入歧途。
    candidates = [here / ".venv", here.parent / ".venv"]
    venv_dir = next((p for p in candidates if p.exists()), None)
    if in_venv:
        detail = f"已激活：{sys.prefix}"
    elif venv_dir:
        detail = f"存在但未激活（{venv_dir}）"
    else:
        detail = "未创建"
    check(
        "虚拟环境",
        in_venv,
        detail=detail,
        fix="python -m venv .venv 然后激活："
            r".\.venv\Scripts\Activate.ps1（Windows）"
            r" / source .venv/bin/activate（macOS）"
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
        print("下一步（Windows PowerShell）：")
        print("  终端 1： py -3 ..\\week01\\mock_server.py")
        print("  终端 2： $env:OPENAI_BASE_URL = \"http://127.0.0.1:8765/v1\"")
        print("           $env:OPENAI_API_KEY  = \"test\"")
        print("           py -3 01_hello_llm.py")
        print()
        print("下一步（macOS / Linux）：")
        print("  终端 1： python3 ../week01/mock_server.py")
        print("  终端 2： OPENAI_BASE_URL=http://127.0.0.1:8765/v1 \\")
        print("           OPENAI_API_KEY=test python3 01_hello_llm.py")
        print()
        print("  Windows 命令差异详见 ai-lab/docs/windows-quickstart.md")
    else:
        print(f"{BAD} 有 {failed} 项必需检查未通过，请先按上面的提示修复。")
        if missing:
            print(f"   缺失文件：{', '.join(missing)}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
