#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后端篇示例验证：把 demos/ 下每个示例都跑一遍，并检查关键输出。

## 为什么要这个脚本

教程里的代码"看起来能跑"和"真的能跑"是两件事。常见的腐烂方式：

  - 依赖升级了，某个 API 改名/废弃（本项目就踩过：`TestClient` 被标废弃）
  - 示例依赖了某个库的新版本行为，换个环境就崩
  - 有人改了示例但没跑，输出和正文对不上了

这个脚本把"示例必须能跑出预期结果"变成一条可执行的检查。
**改了示例就一定要跑它。**

用法：
    py -3 verify_backend_demos.py
    py -3 verify_backend_demos.py --only 05      # 只跑第 05 章

退出码：0 = 全部通过；1 = 有失败。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMOS = HERE / "demos"

# 每个示例：文件名 -> (说明, [必须在输出里出现的片段])
#
# 「必须出现的片段」怎么挑：挑那些"如果代码坏了就会消失"的字符串。
# 不要挑"程序标题"这类永远都在的东西 —— 那样等于没检查。
CHECKS: dict[str, tuple[str, list[str]]] = {
    "01_pydantic_basics.py": (
        "Pydantic 基础：类型即校验 + 结构化错误",
        [
            "req.temperature  = 0.7",          # 默认值生效
            "类型               = Message",     # 嵌套模型被真正解析成对象
            "loc='model'",                     # 缺字段的定位
            "loc='messages.0.content'",        # 嵌套字段的定位
            ".errors() 返回 2 条",              # 结构化错误
        ],
    ),
    "02_pydantic_validate.py": (
        "Pydantic 进阶：validator / schema / 别名",
        [
            "model='deepseek-chat' temperature=0.7",   # strip + round 生效
            "model 不能是空白字符串",                    # field_validator 拦住了
            '"enum"',                                   # Literal 进了 schema
            '"description": "模型对自己这次工具调用正确性的估计，0~1"',   # Field 描述进 schema
            '"pricePerMillion"',                        # 序列化别名
            "exclude_none",                             # 提到 exclude_none
        ],
    ),
    "03_custom_exceptions.py": (
        "自定义异常：靠类型分支而不是字符串",
        [
            "exc.status = 401",
            "检查 API Key 配置（重试无用）",
            "退避后重试",
            "换备用模型降级",
        ],
    ),
    "04_fastapi_minimal.py": (
        "FastAPI：类型注解 -> 自动解析/校验/文档",
        [
            "GET /health  -> 200",
            "422（自动 422，不用自己校验）",
            "['body', 'messages']",       # FastAPI 的校验错误定位
            "POST       /chat",
            "/openapi.json 里登记了 5 个路径",
        ],
    ),
    "05_streaming_sse.py": (
        "流式响应：SSE 三件套",
        [
            "Content-Type: text/event-stream",
            "delta=12",                   # 分段推送
            "meta=1 delta=12 done=1",
            "event: meta",                # 原始字节里能看到 SSE 格式
        ],
    ),
}


def run_pytest_test(path: Path) -> tuple[bool, str]:
    """第 06 章是 pytest 文件，要单独跑。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", path.name, "-q", "--no-header"],
        cwd=str(DEMOS), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    # 从 "7 passed in 0.45s" 里取数字
    m = re.search(r"(\d+) passed", out)
    n = m.group(1) if m else "?"
    ok = proc.returncode == 0 and m is not None
    return ok, f"{n} passed" if ok else out[-400:]


def run_script(path: Path) -> tuple[bool, str, str]:
    proc = subprocess.run(
        [sys.executable, path.name],
        cwd=str(DEMOS), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out, proc.stderr or ""


def main() -> int:
    ap = argparse.ArgumentParser(description="验证后端篇的全部示例")
    ap.add_argument("--only", default="", help="只跑某一章，例如 05")
    args = ap.parse_args()

    print("=" * 78)
    print("  后端篇示例验证 · 每个示例都要真的跑出预期结果")
    print("=" * 78)
    print(f"  目录：{DEMOS}")
    print()

    failures: list[str] = []
    for name, (desc, expected) in sorted(CHECKS.items()):
        if args.only and not name.startswith(args.only):
            continue
        path = DEMOS / name
        if not path.exists():
            print(f"[缺失] {name}")
            failures.append(f"{name}: 文件不存在")
            continue

        ok, out, err = run_script(path)
        # 语法/运行时错误优先报出来，比"缺少片段"有用得多
        if not ok:
            # 过滤掉 PowerShell 包装产生的噪音行，只留 python 的 traceback
            tail = "\n".join(err.strip().splitlines()[-6:])
            print(f"[FAIL] {name}  ({desc})")
            print(f"       运行失败（exit != 0）：\n{tail}")
            failures.append(f"{name}: 运行失败")
            continue

        missing = [s for s in expected if s not in out]
        if missing:
            print(f"[FAIL] {name}  ({desc})")
            for s in missing:
                print(f"       输出里缺少：{s!r}")
            failures.append(f"{name}: 缺少 {len(missing)} 个预期片段")
        else:
            print(f"[ ok ] {name}  ({desc})")
            print(f"       {len(expected)} 个预期片段全部出现")

    # 第 06 章是 pytest 文件
    test_file = DEMOS / "06_testing_api.py"
    if test_file.exists() and (not args.only or "06".startswith(args.only)):
        ok, detail = run_pytest_test(test_file)
        if ok:
            print(f"[ ok ] 06_testing_api.py  (测试接口：夹具 / 参数化 / 流式)")
            print(f"       {detail}")
        else:
            print(f"[FAIL] 06_testing_api.py")
            print(f"       {detail}")
            failures.append("06_testing_api.py: 测试未通过")

    print()
    print("=" * 78)
    if failures:
        print(f"  {len(failures)} 项失败：")
        for f in failures:
            print(f"    - {f}")
    else:
        print("  [ok] 全部示例都能跑，且输出与正文一致")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
