#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强制把指定文件的内容同步到远端（即使 git 认为它是干净的）

## 什么时候需要这个

`push_via_api.py` 只推"**git 认为有变化的文件**"——
这通常是对的，但有一个例外会卡住：

  `.gitattributes` 要求 `eol=lf`，`git add` 会自动把 CRLF 归一成 LF，
  于是**磁盘 CRLF + 索引 LF** 可以共存且 `git status` 干净。
  如果某个文件的远端版本是被写坏的（比如早期 API 推送塞进了 CRLF），
  那么：

    · git 认为本地干净 → 不会把它列入"变化文件"
    · push_via_api 因此永远不会重推它
    · **远端就永久停留在坏版本上**

  实测就是这个情况：README.md 远端是 CRLF 版（18788 字符），
  本地是 LF 版（18188 字符），而 git 什么都不会做。

## 这个脚本做什么

绕过"变化文件"判定，直接把指定文件（默认取 git 索引里的内容，
即归一化后的正确版本）写成 blob，更新远端 tree。

用法：
    python ai-lab/tools/force_sync_file.py ai-lab/projects/project2-rag/README.md
    python ai-lab/tools/force_sync_file.py <path> --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_OWNER = "zhongwenfeng1996"
REPO_NAME = "Python"
ROOT = Path(r"G:\转型")
API = "https://api.github.com"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def token() -> str:
    for exe in (r"C:\Program Files\GitHub CLI\gh.exe", "gh"):
        try:
            out = subprocess.run([exe, "auth", "token"], capture_output=True,
                                 text=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def api(method: str, path: str, tok: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "dsh-force-sync")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        raise SystemExit(f"❌ HTTP {e.code}: {msg[:400]}") from None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="仓库内相对路径（POSIX 风格）")
    ap.add_argument("--message", default="", help="提交信息")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tok = token()
    if not tok:
        print("❌ 拿不到 token")
        return 1

    rel = args.path.replace("\\", "/")
    # **用 git 索引里的内容**（已按 .gitattributes 归一），不是磁盘内容。
    # 这样能保证推上去的版本与 git 记录的一致。
    raw = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
                         capture_output=True).stdout
    if not raw:
        print(f"❌ git 里取不到 {rel}")
        return 1

    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    local_blob = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", f"HEAD:{rel}"],
        capture_output=True, text=True).stdout.strip()

    print("=" * 80)
    print("  强制同步单个文件到远端")
    print("=" * 80)
    print(f"  文件      : {rel}")
    print(f"  本地 blob : {local_blob[:12]}  ({len(raw)} 字节)")
    print(f"  本地 HEAD : {head[:8]}")

    ref = api("GET", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/ref/heads/main", tok)
    remote_head = ref["object"]["sha"]
    commit = api("GET", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/commits/{remote_head}", tok)
    remote_tree = commit["tree"]["sha"]
    print(f"  远端 HEAD : {remote_head[:8]}  tree {remote_tree[:8]}")

    tree = api("GET",
               f"/repos/{REPO_OWNER}/{REPO_NAME}/git/trees/{remote_tree}?recursive=1",
               tok)
    remote_blob = next((e["sha"] for e in tree["tree"] if e["path"] == rel), "")
    print(f"  远端 blob : {remote_blob[:12] if remote_blob else '(不存在)'}")
    print()

    if remote_blob == local_blob:
        print("  ✅ 两边 blob 已经相同，无需同步")
        return 0

    if args.dry_run:
        print("  （--dry-run，不做修改）")
        return 0

    # 1. 建 blob
    blob = api("POST", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/blobs", tok,
               {"content": base64.b64encode(raw).decode("ascii"),
                "encoding": "base64"})
    print(f"  新 blob   : {blob['sha'][:12]}")
    # 关键校验：GitHub 算出的 blob 必须与本地 git 一致。
    # 不一致说明内容转换规则不同 —— 这正是之前 CRLF bug 的表现。
    if blob["sha"] != local_blob:
        print(f"  ⚠️ GitHub 算出的 blob 与本地不同（{blob['sha'][:12]} != "
              f"{local_blob[:12]}）")
        print("     说明内容仍有差异（可能还是行尾）。等 push 完再复查一次。")
    else:
        print("  ✅ blob 与本地 git 完全一致")

    # 2. 建 tree（基于远端 tree，只改这一个文件）
    new_tree = api("POST", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/trees", tok,
                   {"base_tree": remote_tree,
                    "tree": [{"path": rel, "mode": "100644",
                              "type": "blob", "sha": blob["sha"]}]})
    print(f"  新 tree   : {new_tree['sha'][:12]}")

    # 3. 建 commit
    msg = args.message or f"fix(eol): 把 {rel} 的行尾归一为 LF（远端曾是被写坏的 CRLF 版）"
    new_commit = api("POST", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/commits", tok,
                     {"message": msg, "tree": new_tree["sha"],
                      "parents": [remote_head]})
    print(f"  新 commit : {new_commit['sha'][:12]}")

    # 4. 更新 ref
    api("PATCH", f"/repos/{REPO_OWNER}/{REPO_NAME}/git/refs/heads/main", tok,
        {"sha": new_commit["sha"], "force": False})
    print(f"  ✅ refs/heads/main -> {new_commit['sha'][:12]}")
    print()
    print("  现在远端 tree 应该与本地一致（可跑 diff_remote.py 复查）")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
