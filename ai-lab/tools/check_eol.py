#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查受控文件里哪些磁盘内容是 CRLF，而 .gitattributes 要求 LF

## 为什么需要这个检查

`.gitattributes` 规定 `* text=auto eol=lf`，所以 **git 索引里永远是 LF**。
但**磁盘工作区可能是 CRLF** —— `git add` 会自动归一化，
于是 `git status` 干净、看不出问题：**索引 LF 与磁盘 CRLF 和平共处**。

危险的场景只有一个：**任何绕过 git 直接读磁盘内容的工具**。
本仓库里就有一个 —— `ai-lab/tools/push_via_api.py`
（`github.com:443` 经常超时，所以推送走 GitHub Git Data API）。
它把 `read_bytes()` 原样发给 GitHub，于是远端被写成了 CRLF。

实测抓到的后果：
    ai-lab/projects/project2-rag/README.md
      本地 git : 18188 字符 / 0 个 CRLF
      远端 API : 18788 字符 / 600 个 CRLF
    内容一模一样，只是 blob 哈希不同 → "本地与远端 tree 不一致"
    而且 **git 永远不会去修它**（因为 git 认为本地是干净的）

## 这个脚本做什么

列出所有"磁盘 CRLF 但应该是 LF"的受控文本文件，并给出修复建议。
它是**只读检查**，不改文件（修法见 --fix）。

用法：
    python ai-lab/tools/check_eol.py
    python ai-lab/tools/check_eol.py --fix     # 就地转成 LF
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# 二进制扩展名 —— 不做行尾判定，也不该被改
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".whl", ".exe", ".dll", ".so", ".dylib", ".db", ".sqlite3", ".woff",
    ".woff2", ".ttf", ".xlsx", ".docx", ".pptx", ".mp4", ".mp3", ".bin",
    ".esd", ".wim",
}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, encoding="utf-8")
    return [f for f in out.stdout.split("\n") if f.strip()]


def is_text(path: pathlib.Path) -> bool:
    """二进制判定：扩展名黑名单 + 内容含 NUL。"""
    if path.suffix.lower() in BINARY_EXT:
        return False
    try:
        return b"\x00" not in path.read_bytes()[:8192]
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="就地转成 LF")
    args = ap.parse_args()

    files = tracked_files()
    bad: list[tuple[str, int]] = []
    skipped_bin = 0

    for rel in files:
        p = ROOT / rel
        if not p.exists():
            continue
        if not is_text(p):
            skipped_bin += 1
            continue
        raw = p.read_bytes()
        if b"\r\n" in raw:
            bad.append((rel, raw.count(b"\r\n")))

    print("=" * 84)
    print("  行尾符检查 —— 磁盘 CRLF 但 .gitattributes 要求 LF")
    print("=" * 84)
    print(f"  受控文件 {len(files)} 个（其中二进制 {skipped_bin} 个已跳过判定）")
    print()

    if not bad:
        print("  ✅ 没有发现磁盘 CRLF 的文本文件")
        print("     （远端与本地不会因为行尾符产生假性差异）")
        print("=" * 84)
        return 0

    print(f"  ⚠️ 发现 {len(bad)} 个文件磁盘上是 CRLF:")
    print()
    for rel, n in sorted(bad, key=lambda x: -x[1]):
        print(f"    {n:>6} CRLF   {rel}")
    print()
    print("  影响：任何**绕过 git 直接读磁盘**的工具（比如 push_via_api.py）")
    print("        会把这些文件以 CRLF 写到远端，造成 blob 哈希不同。")
    print("        `git status` 看不出来，因为 git 索引里是 LF。")
    print()

    if args.fix:
        print("  --- 就地修复 ---")
        n_fix = 0
        for rel, _ in bad:
            p = ROOT / rel
            raw = p.read_bytes()
            p.write_bytes(raw.replace(b"\r\n", b"\n"))
            n_fix += 1
        print(f"  已把 {n_fix} 个文件转成 LF")
        print("  ⚠️ 这些文件在 git 眼里**没有变化**（索引本来就是 LF），")
        print("     所以不需要提交 —— 修复的是磁盘副本，")
        print("     目的是让 push_via_api.py 不再写出 CRLF。")
    else:
        print("  用 --fix 就地转换（git 不会因此产生改动，因为索引里本来就是 LF）")
    print("=" * 84)
    return 1 if bad and not args.fix else 0


if __name__ == "__main__":
    raise SystemExit(main())
