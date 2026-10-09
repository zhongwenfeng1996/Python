#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 GitHub REST API 推送提交（绕开 git 协议）

## 为什么需要这个

本机 `github.com:443` 经常连不上（超时），但 **`api.github.com:443` 是通的**。
git push 走的是 github.com，所以一直失败；而 REST API 走 api.github.com，能用。

于是用 GitHub 的 Git Data API 手工组装提交：
    blobs → tree → commit → 更新 ref

## 工作方式

读**本地已有的提交**（不重新生成内容），把它相对远端的差异通过 API 重放：
  1. 取远端当前 main 的 SHA 与 tree
  2. 对本次提交里新增/修改的每个文件，创建 blob
  3. 基于远端 tree 建新 tree（只需包含变化的路径）
  4. 建 commit，parent = 远端 HEAD
  5. 把 refs/heads/main 指到新 commit

## 重要提醒

这样做出来的 commit SHA **与本地不同**（作者/时间戳/父提交差异）。
推完之后本地与远端会分叉，需要用 `git reset --hard` 对齐到 API 生成的提交，
否则下次 git push 会因为历史不一致而失败。

用法：
    python ai-lab/tools/push_via_api.py            # 预览（不推送）
    python ai-lab/tools/push_via_api.py --apply    # 真推送
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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = Path(r"G:\转型")
OWNER = "zhongwenfeng1996"
NAME = "Python"
API = "https://api.github.com"


def get_token() -> str:
    """从 gh CLI 取 token（避免把 token 写进代码或环境文件）。"""
    for exe in (r"C:\Program Files\GitHub CLI\gh.exe", "gh"):
        try:
            p = subprocess.run([exe, "auth", "token"], capture_output=True,
                               text=True, timeout=30)
            tok = (p.stdout or "").strip()
            if tok:
                return tok
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("取不到 gh token，请先 gh auth login")


def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = f"{API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"API {method} {path} 失败：HTTP {exc.code}\n{detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"API {method} {path} 网络失败：{exc}") from exc


def git(*args: str) -> str:
    p = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{p.stderr[:300]}")
    return p.stdout


def git_modes(commit: str) -> dict[str, str]:
    """
    从 git 里读出每个文件在**该提交中真实的 mode**。

    ⚠️ 这里踩过一次：第一版按"文件是否以 #! 开头"猜 100755/100644。
    那是错的 —— 本仓库在 Windows 上 `core.fileMode=false`，
    所有文件都是 100644。按 shebang 猜会让推送后的权限位与仓库不一致。
    **正确做法是问 git，而不是猜。**
    """
    out = git("ls-tree", "-r", commit)
    modes: dict[str, str] = {}
    for line in out.splitlines():
        # 格式: <mode> SP <type> SP <sha> TAB <path>
        if "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3:
            modes[path] = parts[0]
    return modes


def changed_files(commit: str) -> list[str]:
    """列出该提交相对其父提交新增/修改的文件（不含删除）。"""
    out = git("show", "--name-status", "--format=", commit)
    files = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        # 只处理新增(A)/修改(M)；删除(D)也支持但要单独处理
        files.append((status, path))
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正推送（默认只预览）")
    ap.add_argument("--commit", default="HEAD", help="要推送的本地提交")
    args = ap.parse_args()

    token = get_token()
    sha_local = git("rev-parse", args.commit).strip()
    msg = git("log", "-1", "--format=%B", args.commit).strip()
    author_name = git("log", "-1", "--format=%an", args.commit).strip()
    author_email = git("log", "-1", "--format=%ae", args.commit).strip()

    print("=" * 78)
    print("  用 GitHub API 推送提交")
    print("=" * 78)
    print(f"  本地提交: {sha_local[:8]}  {msg.splitlines()[0]}")
    print(f"  作者    : {author_name} <{author_email}>")
    print()

    # 远端当前状态
    ref = api("GET", f"/repos/{OWNER}/{NAME}/git/ref/heads/main", token)
    remote_sha = ref["object"]["sha"]
    print(f"  远端 main: {remote_sha[:8]}")
    print()

    # 安全检查：远端的提交必须是本地的祖先（否则会覆盖别人的提交）
    is_ancestor = subprocess.run(
        ["git", "-C", str(REPO), "merge-base", "--is-ancestor", remote_sha, sha_local],
        capture_output=True)
    if is_ancestor.returncode != 0:
        print("  ❌ 远端的提交不是本地的祖先 —— 远端可能有其他人的新提交。")
        print("     为了不覆盖别人的工作，脚本拒绝继续。请先手动 git fetch 合并。")
        return 2
    print("  ✅ 远端提交是本地祖先，可以安全快进")
    print()

    # 远端 tree
    remote_commit = api("GET", f"/repos/{OWNER}/{NAME}/git/commits/{remote_sha}", token)
    base_tree = remote_commit["tree"]["sha"]
    print(f"  远端 tree : {base_tree[:8]}")

    # 本次要推送的文件
    files = changed_files(args.commit)
    modes = git_modes(args.commit)
    print(f"  本次变更  : {len(files)} 个文件")
    print()

    tree_items = []
    for status, path in files:
        full = REPO / path
        if status == "D":
            # 删除：sha 传 null
            tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            print(f"    [删除] {path}")
            continue
        if not full.exists():
            print(f"    [跳过] {path}（本地不存在）")
            continue
        raw = full.read_bytes()
        # mode 以 git 记录的为准（见 git_modes 的说明：不要按 shebang 猜）
        mode = modes.get(path, "100644")
        if args.apply:
            blob = api("POST", f"/repos/{OWNER}/{NAME}/git/blobs", token,
                       {"content": base64.b64encode(raw).decode("ascii"),
                        "encoding": "base64"})
            blob_sha = blob["sha"]
        else:
            blob_sha = "(preview)"
        tree_items.append({"path": path, "mode": mode, "type": "blob", "sha": blob_sha})
        print(f"    [{status}] {path}  ({len(raw)} 字节, mode={mode})")

    print()
    if not args.apply:
        print("  —— 预览模式，未推送。加 --apply 真正执行 ——")
        return 0

    # 建 tree
    tree = api("POST", f"/repos/{OWNER}/{NAME}/git/trees", token,
               {"base_tree": base_tree, "tree": tree_items})
    print(f"  新 tree   : {tree['sha'][:8]}")

    # 建 commit
    commit = api("POST", f"/repos/{OWNER}/{NAME}/git/commits", token,
                 {"message": msg, "tree": tree["sha"], "parents": [remote_sha],
                  "author": {"name": author_name, "email": author_email}})
    print(f"  新 commit : {commit['sha'][:8]}")

    # 更新 ref
    api("PATCH", f"/repos/{OWNER}/{NAME}/git/refs/heads/main", token,
        {"sha": commit["sha"], "force": False})
    print(f"  ✅ refs/heads/main -> {commit['sha'][:8]}")
    print()
    print("=" * 78)
    print("  推送完成（通过 API）。")
    print()
    print("  ⚠️ 这个提交的 SHA 与本地不同（作者时间戳/父提交有差异）。")
    print("     要让本地与远端一致，请执行：")
    print(f"       git -C G:\\转型 fetch origin")
    print(f"       git -C G:\\转型 reset --hard origin/main")
    print("     （本地文件内容不会变 —— 两边内容是一样的）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
