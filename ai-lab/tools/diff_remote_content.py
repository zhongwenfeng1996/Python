#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对比两边的 README 内容，判断差异是"行尾符"还是"真内容"

## 背景

`ai-lab/tools/diff_remote.py` 报出：本地与远端只有 README.md 的 blob 不同，
文件数量一致（130 vs 130）。

两种可能：
  A. **行尾符**（CRLF vs LF）—— 无害。仓库的 .gitattributes 规定
     `* text=auto eol=lf`，索引里存 LF；但 API 推送是直接把磁盘内容
     按原样发出去的，如果当时磁盘上是 CRLF，远端就会存成 CRLF。
  B. **真内容差异** —— 危险，说明推送漏了某些改动。

区分方法：把两边都规范化（去行尾、去尾空白）再比。
如果规范化后相同 → 是 A；不同 → 是 B，并打出真正的差异行。

用法：
    python ai-lab/tools/diff_remote_content.py <相对路径>
"""

from __future__ import annotations

import base64
import difflib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "zhongwenfeng1996/Python"
ROOT = Path(r"G:\转型")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def gh_token() -> str:
    for exe in (r"C:\Program Files\GitHub CLI\gh.exe", "gh"):
        try:
            out = subprocess.run([exe, "auth", "token"], capture_output=True,
                                 text=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def api(path: str, token: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com/{path}")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "dsh-diff")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    rel = sys.argv[1] if len(sys.argv) > 1 else \
        "ai-lab/projects/project2-rag/README.md"
    token = gh_token()
    if not token:
        print("❌ 拿不到 token")
        return 1

    # 本地：用 git show 取**索引里的内容**（LF），不是磁盘内容
    local_git = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
        capture_output=True).stdout.decode("utf-8")
    # 本地磁盘内容（可能是 CRLF）
    disk_path = ROOT / rel
    disk = disk_path.read_text(encoding="utf-8") if disk_path.exists() else ""

    # 远端
    d = api(f"repos/{REPO}/contents/{rel}", token)
    remote = base64.b64decode(d["content"]).decode("utf-8")

    def norm(s: str) -> str:
        return "\n".join(line.rstrip() for line in
                         s.replace("\r\n", "\n").replace("\r", "\n").split("\n"))

    print("=" * 84)
    print(f"  对比 {rel}")
    print("=" * 84)
    print(f"  本地 git（索引）: {len(local_git)} 字符  "
          f"CRLF={local_git.count(chr(13) + chr(10))}")
    print(f"  本地 磁盘        : {len(disk)} 字符  "
          f"CRLF={disk.count(chr(13) + chr(10))}")
    print(f"  远端 API         : {len(remote)} 字符  "
          f"CRLF={remote.count(chr(13) + chr(10))}")
    print()

    if norm(local_git) == norm(remote):
        print("  ✅ 规范化（去行尾差异）后**完全相同** → 差异纯粹是行尾符")
        print("     影响：仅 blob 哈希不同，内容没有丢失。")
    else:
        print("  ❌ 规范化后**仍然不同** → 有真实内容差异")
        print()
        a = norm(local_git).split("\n")
        b = norm(remote).split("\n")
        diff = list(difflib.unified_diff(b, a, fromfile="远端", tofile="本地",
                                         lineterm="", n=2))
        print(f"  差异行数: {len([x for x in diff if x[:1] in '+-' and x[:3] not in ('+++','---')])}")
        print("  （前 40 行）")
        for line in diff[:40]:
            print(f"    {line}")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
