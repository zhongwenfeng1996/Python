#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
环境一致性校验：验证"文档里写的环境"和"真实环境"是否一致。

## 为什么需要这个脚本

这个仓库已经出过一次事故：README 声称数据集"最近 30 天活跃下跌"，
而数据里是**上涨 2.6 倍**。文档说谎的代价很高 ——
它会让你按错误的预期去调试，浪费几小时，最后怀疑自己。

环境相关的断言同样容易腐烂，而且更难发现：
  - 文档说 `.venv` 在仓库根，实际建在 ai-lab 下
  - 文档说用 Python 3.12，实际某个脚本在用别的解释器
  - 文档说 `.ps1` 带 BOM，某次编辑把 BOM 弄丢了（编辑器很爱干这事）
  - 文档说 pip 走镜像，实际配置在非 ASCII 路径下失效

这些都不是"代码 bug"，测试永远抓不到，但每一项都能让人卡半天。

## 与其他检查脚本的分工

| 脚本 | 回答的问题 |
|---|---|
| `ai-lab/tools/env_report.py` | **手边有什么**（工具、解释器、包、编码） |
| `ai-lab/scripts/verify_dataset.py` | **数据里有什么**（31 条断言，数据集可信吗） |
| 本脚本 | **文档说的是不是真的**（文档与环境漂移了吗） |

用法：
    python ai-lab/tools/check_doc_env.py
    python ai-lab/tools/check_doc_env.py --quiet

退出码：0 = 全部一致；1 = 有漂移（每条都会指出"文档在哪说的、实际是什么"）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AI_LAB = REPO / "ai-lab"

OK = "[ok]"
BAD = "[DRIFT]"
INFO = "[..]"

Results = list[tuple[str, bool, str, str]]   # (断言, 通过, 实际值, 文档出处)


def check(results: Results, desc: str, passed: bool, actual: str, source: str) -> None:
    results.append((desc, passed, actual, source))


# ----------------------------------------------------------------------
# 1. 解释器与虚拟环境
# ----------------------------------------------------------------------
def check_interpreter(results: Results) -> None:
    """文档：Python 装在 G:\\Python\\Python312，.venv 在仓库根。"""
    expected_python = Path(r"G:\Python\Python312\python.exe")
    check(results, "独立 Python 在文档所述位置",
          expected_python.exists(),
          str(expected_python) if expected_python.exists() else "不存在",
          "README.md「环境要求」")

    venv_python = REPO / ".venv" / "Scripts" / "python.exe"
    check(results, "虚拟环境在仓库根（不是 ai-lab 下）",
          venv_python.exists(),
          str(venv_python.relative_to(REPO)) if venv_python.exists() else "不存在",
          "README.md「环境要求」/ windows-quickstart §8")

    if not venv_python.exists():
        return

    # .venv 必须基于独立 Python，而不是 DSH 自带运行时
    out = subprocess.run(
        [str(venv_python), "-c",
         "import sys,json;print(json.dumps({'v':list(sys.version_info[:3]),"
         "'base':sys.base_prefix,'exe':sys.executable}))"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    try:
        info = json.loads(out.stdout.strip())
    except json.JSONDecodeError:
        check(results, "读取 venv 信息", False, out.stdout + out.stderr,
              ".venv 状态")
        return

    check(results, "venv 的 base 是独立 Python（不是 DSH 自带运行时）",
          "dsh-runtimes" not in info["base"].lower(),
          info["base"],
          "STATUS.md「环境状态」")

    check(results, "venv 的 Python 版本是 3.12",
          info["v"][:2] == [3, 12],
          ".".join(map(str, info["v"])),
          "README.md「环境要求」")

    # 关键依赖是否齐全（文档承诺 .venv 里已装好）
    code = ("import importlib.util as u;"
            "mods=['fastapi','uvicorn','httpx','pydantic','pytest','pytest_asyncio'];"
            "missing=[m for m in mods if u.find_spec(m) is None];"
            "print(','.join(missing))")
    out = subprocess.run([str(venv_python), "-c", code],
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    missing = out.stdout.strip()
    check(results, "venv 里关键依赖齐全",
          not missing, missing or "全部就位",
          "requirements.txt / README.md")


# ----------------------------------------------------------------------
# 2. .ps1 的 BOM（文档承诺，且极易被编辑器和工具弄丢）
# ----------------------------------------------------------------------
def check_ps1_bom(results: Results) -> None:
    scripts = sorted(REPO.rglob("*.ps1"))
    # 排除虚拟环境与工具链目录
    scripts = [s for s in scripts
               if not any(p in s.parts for p in (".venv", ".tools", "node_modules"))]
    if not scripts:
        check(results, "存在 .ps1 脚本", False, "一个都没有", "windows-quickstart §0.1")
        return
    bad = []
    for path in scripts:
        if not path.read_bytes().startswith(b"\xef\xbb\xbf"):
            bad.append(path.relative_to(REPO).as_posix())
    check(results, f"全部 {len(scripts)} 个 .ps1 带 UTF-8 BOM",
          not bad, "缺少 BOM: " + ", ".join(bad) if bad else "全部带 BOM",
          "windows-quickstart §0.1（5.1 读无 BOM 脚本会报误导性语法错误）")


# ----------------------------------------------------------------------
# 3. .gitignore 必须真正忽略生成物
# ----------------------------------------------------------------------
def check_gitignore(results: Results) -> None:
    cases = [
        (REPO / ".venv", "虚拟环境"),
        (REPO / "logs", "运行日志"),
        (AI_LAB / "projects" / "project1-stream-chat" / "logs", "项目日志"),
        (AI_LAB / "projects" / "project1-stream-chat" / "eval" / "report.json",
         "评估报告（生成物）"),
        (AI_LAB / "data" / "out" / "events.csv", "数据集产物（可重建）"),
    ]
    for path, desc in cases:
        if not path.exists():
            check(results, f".gitignore 覆盖 {desc}", True, "尚不存在，跳过",
                  ".gitignore")
            continue
        out = subprocess.run(["git", "check-ignore", "-q", str(path)],
                             cwd=REPO, capture_output=True, timeout=30)
        check(results, f".gitignore 覆盖 {desc}",
              out.returncode == 0,
              "已被忽略" if out.returncode == 0 else "**会被提交进去**",
              ".gitignore")


# ----------------------------------------------------------------------
# 4. 文档里承诺的文档/脚本真的存在
# ----------------------------------------------------------------------
def check_doc_references(results: Results) -> None:
    """把根 README 里提到的仓内路径抽出来，逐个验证存在。"""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    # 匹配 markdown 链接里的相对路径（排除 URL、锚点、绝对路径）
    links = re.findall(r"\]\(([^)]+)\)", readme)
    missing = []
    checked = 0
    for raw in links:
        target = raw.strip().strip("<>")
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        if "\\" in target and ":" in target:      # 绝对 Windows 路径
            continue
        candidate = (REPO / target).resolve()
        checked += 1
        if not candidate.exists():
            missing.append(target)
    check(results, f"根 README 的 {checked} 个仓内链接都有效",
          not missing, "失效: " + ", ".join(missing) if missing else "全部有效",
          "README.md")


# ----------------------------------------------------------------------
# 5. 模拟上游行为与文档一致
# ----------------------------------------------------------------------
def check_mock_behaviour(results: Results) -> None:
    """
    文档承诺 mock 支持 /stats、/set-fail-first、--jitter。
    这三样是评估与测试的基础设施，坏了会让断言静默失效。
    """
    mock = AI_LAB / "week01" / "mock_server.py"
    if not mock.exists():
        check(results, "mock_server.py 存在", False, "缺失", "week01/README.md")
        return
    text = mock.read_text(encoding="utf-8")
    for feature, desc, source in [
        ("/stats", "请求计数端点（测试断言降级用）", "project1 README"),
        ("/set-fail-first", "动态失败端点（测试降级用）", "project1 README"),
        ("--jitter", "抖动模式（验证稳定性指标用）", "project1 README「评估」"),
        ("ThreadingHTTPServer", "多线程（中断测试需要并发）", "tests/conftest.py"),
    ]:
        check(results, f"mock 支持 {feature}",
              feature in text, "已实现" if feature in text else "**缺失**", source)


# ----------------------------------------------------------------------
# 6. 测试文件与文档承诺的数量对得上
# ----------------------------------------------------------------------
def check_test_counts(results: Results) -> None:
    """
    文档里写了测试条数（"28 passed"、"24 passed"）。数字会对不上，
    因为加测试时没人会回头改文档 —— 所以要让脚本盯着。

    用 `pytest --collect-only` 拿**真实会被执行的用例数**，而不是数源码里的
    `def test_`。

    为什么（这个脚本第一版就踩了）：`@pytest.mark.parametrize` 会把一个函数
    展开成多条用例。MCP 那个"8 个注入载荷"就是一个函数变 8 条，
    静态数函数会得出 17，而实际是 24 —— 于是脚本报了一个假漂移。
    校验脚本自己算错，比不校验更糟：它会让你去"修"一个没坏的东西。
    """
    expectations = [
        (AI_LAB / "projects" / "project1-stream-chat", "project1 README"),
        (AI_LAB / "projects" / "mcp-minimal", "mcp-minimal README"),
    ]
    for project, source in expectations:
        tests_dir = project / "tests"
        readme = project / "README.md"
        if not tests_dir.exists() or not readme.exists():
            check(results, f"{project.name} 测试与 README 存在", False,
                  "缺失", source)
            continue

        # 文档里声明的数字（形如 "28 passed" / "24 条"）
        text = readme.read_text(encoding="utf-8")
        claimed = re.findall(r"(\d+)\s*(?:passed|条(?:协议)?测试|条测试)", text)
        claimed_nums = sorted({int(n) for n in claimed})

        # 真实收集到的用例数
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             str(tests_dir)],
            cwd=str(project), capture_output=True, text=True,
            encoding="utf-8", timeout=120,
        )
        collected = None
        for line in reversed((out.stdout or "").splitlines()):
            m = re.search(r"(\d+)\s+tests?\s+collected", line)
            if m:
                collected = int(m.group(1))
                break
        if collected is None:
            # 退回到静态计数（并把参数化算作至少 1 条）
            collected = sum(
                len(re.findall(r"^\s*(?:async\s+)?def test_", p.read_text(encoding="utf-8"), re.M))
                for p in tests_dir.rglob("test_*.py")
            )
            note = f"{collected}（静态计数，pytest 收集失败）"
        else:
            note = str(collected)

        check(results, f"{project.name} 可收集到的用例数 >= 文档所述",
              bool(claimed_nums) and collected >= max(claimed_nums, default=0),
              f"实际 {note}，文档声明 {claimed_nums or '无'}",
              source)


def check_readability_tool(results: Results) -> None:
    """
    教程可读性检查本身必须存在且通过。

    为什么把它也纳入：那个脚本负责发现"没头没尾的代码片段"，
    如果它自己坏了（语法错误、或被人改成永远返回 0），
    就没有任何东西会提醒你 —— 而它要防的正是"文档让新手看不懂"这类问题。
    """
    tool = AI_LAB / "tools" / "check_docs_readability.py"
    if not tool.exists():
        check(results, "教程可读性检查脚本存在", False, "缺失", "ai-lab/tools/")
        return
    out = subprocess.run(
        [sys.executable, str(tool)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    check(results, "教程可读性检查通过（无新的悬空代码片段）",
          out.returncode == 0,
          "通过（已知误报不计入失败）" if out.returncode == 0 else
          f"exit={out.returncode}：{(out.stdout or '')[-200:]}",
          "ai-lab/tools/check_docs_readability.py")


def check_duplication_tool(results: Results) -> None:
    """
    重复检测也要纳入 —— 它是"强行补充内容"最直接的发现手段。

    这个仓库出过一次：把 86 行的 bytes/str 说明加进第 04 章，
    而第 10 章本来就讲过同一件事。两处说法还不一致，
    读者不知道该信哪一处。文件存在性检查、悬空变量检查都抓不到这个。
    """
    tool = AI_LAB / "tools" / "check_docs_duplication.py"
    if not tool.exists():
        check(results, "教程重复检测脚本存在", False, "缺失", "ai-lab/tools/")
        return
    out = subprocess.run(
        [sys.executable, str(tool)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    check(results, "教程没有新的重复代码块",
          out.returncode == 0,
          "通过（已核实的刻意重复不计入失败）" if out.returncode == 0 else
          f"exit={out.returncode}：{(out.stdout or '')[-200:]}",
          "ai-lab/tools/check_docs_duplication.py")


def check_prerequisite_tool(results: Results) -> None:
    """
    前置知识倒挂检查 —— 现在是**硬规则**（必须 0 处）。

    曾经这里写的是"仅记录，不算失败"，因为原设计就有 21 处倒挂，
    修它们要动章节顺序。2026-10 重排章节后倒挂降到 0，
    规则随之升级：**再出现倒挂就是失败。**

    为什么值得当硬规则：新手看到没学过的写法不会"跳过继续读"，
    而是卡在那里怀疑自己 —— 这是教程质量里最容易被忽视、代价又最高的一项。
    """
    tool = AI_LAB / "tools" / "check_doc_prerequisites.py"
    if not tool.exists():
        check(results, "前置知识倒挂检查脚本存在", False, "缺失", "ai-lab/tools/")
        return
    out = subprocess.run(
        [sys.executable, str(tool)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    stdout = out.stdout or ""
    import re as _re
    # 匹配新输出："共 N 处倒挂" 或 "[ok] 没有发现前置倒挂（两层都干净）"
    m = _re.search(r"共 (\d+) 处倒挂", stdout)
    if m:
        count, ok = m.group(1), False
        summary = f"{count} 处（两层合计）"
    elif "没有发现前置倒挂" in stdout:
        ok, summary = True, "0 处（基础篇 10 章 + 后端篇 6 个示例）"
    else:
        ok, summary = False, f"无法解析输出：{stdout[-200:]}"
    check(results, "零前置倒挂（第 N 章只用第 1..N 章教过的写法）",
          ok, summary, "ai-lab/tools/check_doc_prerequisites.py")


def check_backend_demos(results: Results) -> None:
    """
    后端篇（第二层）的示例必须全部能跑，且输出与正文一致。

    为什么单列一项：后端篇的示例是**完整可运行文件**，而且依赖 FastAPI /
    Pydantic / httpx 的具体版本行为（本项目就踩过 `TestClient` 被标废弃）。
    不把它们纳入日常检查，改一次依赖或改一次示例就可能悄悄坏掉。
    """
    verifier = AI_LAB / "python_basics" / "backend" / "verify_backend_demos.py"
    if not verifier.exists():
        check(results, "后端篇示例验证脚本存在", False, "缺失",
              "ai-lab/python_basics/backend/")
        return
    out = subprocess.run(
        [sys.executable, str(verifier)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    ok = out.returncode == 0
    # 从输出里抠出 "N 项失败" 或成功那行，作为摘要
    summary = "全部示例通过" if ok else (out.stdout or "")[-200:]
    check(results, "后端篇 6 个示例都能跑且输出与正文一致", ok, summary,
          "ai-lab/python_basics/backend/verify_backend_demos.py")


def main() -> int:
    ap = argparse.ArgumentParser(description="校验文档描述的环境与真实环境是否一致")
    ap.add_argument("--quiet", action="store_true", help="只打印漂移项与总结")
    args = ap.parse_args()

    results: Results = []
    print("=" * 78)
    print("  环境一致性校验 · 文档说的和实际的是不是一回事")
    print("=" * 78)
    print(f"  仓库根：{REPO}")
    print()

    check_interpreter(results)
    check_ps1_bom(results)
    check_gitignore(results)
    check_doc_references(results)
    check_mock_behaviour(results)
    check_test_counts(results)
    check_readability_tool(results)
    check_duplication_tool(results)
    check_prerequisite_tool(results)
    check_backend_demos(results)

    passed = sum(1 for _, ok, _, _ in results if ok)
    for desc, ok, actual, source in results:
        if args.quiet and ok:
            continue
        mark = OK if ok else BAD
        print(f"{mark} {desc}")
        print(f"       实际: {actual}")
        if not ok:
            print(f"       文档: {source}")
    if not args.quiet:
        print()

    failed = len(results) - passed
    print("=" * 78)
    if failed:
        print(f"{BAD} {failed}/{len(results)} 项与文档不一致。")
        print("     改哪边都行，但**不要让文档继续说谎** —— "
              "这个仓库已经被文档坑过一次（数据集陷阱 6），代价是几小时的错误排查。")
    else:
        print(f"{OK} 全部 {len(results)} 项一致，文档描述的环境是可信的。")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
