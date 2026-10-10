#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对比本地 HEAD 与远端 main 的文件集合（走 API，绕开 github.com:443）

## 为什么要单独一个脚本

`git push` 经常超时，所以推送走 GitHub Git Data API。
但 API 推送需要手工组装 tree，一旦哪里漏了文件，就会静默地少东西 ——
而"本地与远端 tree 不一致"这个信号本身说明不了缺什么。

这个脚本用两个途径取文件集合再对比：
  · 本地：git ls-files（或 git ls-tree -r HEAD）
  · 远端：GitHub trees API ?recursive=1

输出：只在本地的、只在远端的、两边都有但 blob 不同的。

⚠️ 必须用脚本而不是内联命令：
   PowerShell 会把 gh 的 --jq 表达式吃掉引号，
   实测因此报过 "function not defined: blob/0" 这种莫名其妙的错。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
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
    token = gh_token()
    if not token:
        print("❌ 拿不到 GitHub token")
        return 1

    # 本地
    local_raw = subprocess.run(
        ["git", "-C", str(ROOT), "ls-tree", "-r", "HEAD"],
        capture_output=True, text=True, encoding="utf-8").stdout
    local: dict[str, str] = {}
    for line in local_raw.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ == "blob":
            local[path] = sha

    # 远端
    ref = api(f"repos/{REPO}/git/ref/heads/main", token)
    commit = api(f"repos/{REPO}/git/commits/{ref['object']['sha']}", token)
    tree = api(f"repos/{REPO}/git/trees/{commit['tree']['sha']}?recursive=1", token)
    remote = {e["path"]: e["sha"] for e in tree["tree"] if e["type"] == "blob"}

    print("=" * 84)
    print("  本地 HEAD  vs  远端 main")
    print("=" * 84)
    print(f"  本地文件数: {len(local)}")
    print(f"  远端文件数: {len(remote)}")
    print(f"  远端 commit: {ref['object']['sha'][:8]}   tree: {commit['tree']['sha'][:8]}")
    print()

    only_local = sorted(set(local) - set(remote))
    only_remote = sorted(set(remote) - set(local))
    diff = sorted(p for p in set(local) & set(remote) if local[p] != remote[p])

    if only_local:
        print(f"  ❗ 只在本地（**远端缺失**，{len(only_local)} 个）:")
        for p in only_local:
            print(f"      {p}")
        print()
    if only_remote:
        print(f"  ❗ 只在远端（本地缺失，{len(only_remote)} 个）:")
        for p in only_remote:
            print(f"      {p}")
        print()
    if diff:
        print(f"  ❗ 内容不同（{len(diff)} 个）:")
        for p in diff:
            print(f"      {p}")
            print(f"        本地 {local[p][:12]}  远端 {remote[p][:12]}")
        print()

    if not (only_local or only_remote or diff):
        print("  ✅ 两边文件集合与内容完全一致")
    else:
        n = len(only_local) + len(only_remote) + len(diff)
        print(f"  合计不一致: {n} 处")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
