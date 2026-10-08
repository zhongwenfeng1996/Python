#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
环境自检（Windows 优先，跨平台可用）

在开始任何一个练习/项目之前跑一次，确认手边到底有什么工具：

    python ai-lab/tools/env_report.py

为什么要写这个：
  前端转 Python 的第一个坑不是语法，是"你以为装了，其实没装"。
  典型例子 —— Windows 上的 `python` 可能只是 Microsoft Store 的占位符，
  运行时报 "Python was not found but can be installed from the Microsoft Store"。
  这个脚本把这类问题在你写第一行业务代码之前就暴露出来。

零依赖，只用标准库。
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

OK = "[ok]"
MISS = "[--]"
WARN = "[!!]"


def report_commands() -> None:
    """检查关键可执行文件是否真的可用（不只是文件存在）。"""
    print("—— 命令行工具 ——")
    tools = {
        "git": "版本控制（必须）",
        "python": "解释器（Windows 上可能是 Store 占位符，务必看下一节结论）",
        "node": "前端项目（项目一需要）",
        "pnpm": "前端包管理（比 npm 快，可选）",
        "docker": "Postgres / 部署（W5 之后需要）",
        "psql": "本机 Postgres 客户端（可选）",
    }
    for name, desc in tools.items():
        path = shutil.which(name)
        mark = OK if path else MISS
        print(f"  {mark} {name:<8} {path or '未找到':<60} {desc}")
    print()


def report_python() -> bool:
    """报告当前解释器，并判断它是否是可长期使用的真实安装。"""
    print("—— 当前 Python 解释器 ——")
    exe = Path(sys.executable)
    print(f"  版本      {platform.python_version()}")
    print(f"  路径      {exe}")

    # Store 占位符 / 沙箱运行时 / 系统目录，都不适合作为长期开发环境
    lowered = str(exe).lower()
    suspicious = []
    if "windowsapps" in lowered:
        suspicious.append("位于 WindowsApps，这是 Microsoft Store 的占位符或商店版")
    if "dsh-runtimes" in lowered or "dsh\\" in lowered:
        suspicious.append("位于 DSH 自带运行时，随工具更新可能消失，不要依赖它")
    if ".tools" in lowered:
        suspicious.append("位于仓库内的 .tools，属于本地工具链（已被 git 忽略）")

    if suspicious:
        print()
        for item in suspicious:
            print(f"  {WARN} {item}")
        print(f"  {WARN} 建议按 docs/windows-quickstart.md 装一个独立 Python 3.12，")
        print("       并把虚拟环境建在项目目录里。")
        return False

    print(f"  {OK} 看起来是一个正常的独立安装")
    print()
    return True


def report_venv() -> None:
    print("—— 虚拟环境 ——")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        print(f"  {OK} 已激活：{sys.prefix}")
    else:
        print(f"  {WARN} 未激活虚拟环境。现在装的包会进全局，这是新手第一大坑。")
        print("       Windows:  .venv\\Scripts\\Activate.ps1")
        print("       macOS:    source .venv/bin/activate")
    print()


def report_encoding() -> None:
    """Windows 上中文乱码的根源都能在这里看到。"""
    print("—— 编码（Windows 中文乱码排查） ——")
    print(f"  stdout 编码     {sys.stdout.encoding}")
    print(f"  文件系统编码    {sys.getfilesystemencoding()}")
    print(f"  PYTHONUTF8      {os.environ.get('PYTHONUTF8', '未设置')}")
    print(f"  PYTHONIOENCODING{os.environ.get('PYTHONIOENCODING', '未设置')}")
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        print(f"  {WARN} stdout 不是 UTF-8，打印中文可能乱码。")
        print("       临时解决：$env:PYTHONUTF8=\"1\"   （或在代码里显式 encoding=\"utf-8\"）")
    else:
        print(f"  {OK} 编码正常")
    print()


def report_env_file() -> None:
    print("—— API 配置 ——")
    key = os.environ.get("OPENAI_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("MODEL", "")
    if key:
        print(f"  {OK} OPENAI_API_KEY 已设置（{key[:6]}...）")
    else:
        print(f"  {MISS} OPENAI_API_KEY 未设置 —— 只能用 week01/mock_server.py 离线练习")
        print('       PowerShell:  $env:OPENAI_API_KEY="sk-你的key"')
    print(f"  {'--'} OPENAI_BASE_URL  {base or '未设置（将用代码里的默认值）'}")
    print(f"  {'--'} MODEL           {model or '未设置（将用代码里的默认值）'}")
    print()


def report_repo_layout() -> None:
    """确认关键文件都在——换了机器/重新 clone 后能一眼看出缺什么。"""
    print("—— 仓库结构 ——")
    root = Path(__file__).resolve().parents[2]
    expected = [
        "前端转LLM应用工程师-学习计划.md",
        "ai-lab/README.md",
        "ai-lab/week01/chat.py",
        "ai-lab/week01/mock_server.py",
        "ai-lab/python_basics/README.md",
        "ai-lab/python_basics/docs/README.md",
        "ai-lab/data/schema.sql",
        "ai-lab/scripts/generate_saas_data.py",
        "ai-lab/semantics/metrics.yml",
        "ai-lab/docs/windows-quickstart.md",
    ]
    missing = []
    for rel in expected:
        exists = (root / rel).exists()
        if not exists:
            missing.append(rel)
        print(f"  {OK if exists else MISS} {rel}")
    print()
    if missing:
        print(f"  {WARN} 缺少 {len(missing)} 个文件，仓库可能不完整。")
        print()


def main() -> int:
    print("=" * 74)
    print("  AI 转型实验室 · 环境自检")
    print("=" * 74)
    print(f"  平台  {platform.system()} {platform.release()}")
    print(f"  工作目录  {Path.cwd()}")
    print()

    report_commands()
    python_ok = report_python()
    report_venv()
    report_encoding()
    report_env_file()
    report_repo_layout()

    print("=" * 74)
    if python_ok:
        print(f"{OK} 环境基本就绪。下一步：")
    else:
        print(f"{WARN} 当前解释器不适合长期使用。先照 docs/windows-quickstart.md 装好 Python，再回来。")
        print("     然后：")
    print("     1) ai-lab/python_basics/check_env.py     —— 检查学习材料是否齐全")
    print("     2) ai-lab/week01/mock_server.py          —— 起本地 mock 服务")
    print("=" * 74)
    return 0 if python_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
