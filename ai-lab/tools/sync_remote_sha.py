#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
让本地 HEAD 与远端 SHA 完全一致 —— 不依赖 fetch（网络不通时也能用）

## 问题

用 GitHub API 推送时，GitHub 服务端会生成新的 commit 对象
（作者/提交时间戳、committer 与本地不同），于是：
    · 内容（tree）完全相同
    · 但 commit SHA 不同 → 本地与远端"分叉"

正常情况下 fetch 一下再 reset --hard origin/main 就解决了。
但本机 github.com 时通时断，fetch 经常失败。

## 解法

既然 tree 一样，就可以在**本地重放**出那个 commit 对象：
  git commit-tree <tree> -p <parent> -m <message>
再把 author/committer 的姓名、邮箱、时间设成与远端一致
（用 GIT_AUTHOR_DATE / GIT_COMMITTER_DATE 环境变量），
本地算出的 SHA 就会**逐位等于**远端。

前提是本地已经有该 tree 和 parent 对象 —— 因为它们都在本地历史里
（我用 local 提交的内容推的，tree hash 已在本地）。

## 用法

    python ai-lab/tools/sync_remote_sha.py            # 只检查、报告
    python ai-lab/tools/sync_remote_sha.py --apply    # 真对齐
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = Path(r"G:\转型")
OWNER, NAME = "zhongwenfeng1996", "Python"


def get_token() -> str:
    for exe in (r"C:\Program Files\GitHub CLI\gh.exe", "gh"):
        try:
            p = subprocess.run([exe, "auth", "token"], capture_output=True,
                               text=True, timeout=30)
            if (p.stdout or "").strip():
                return p.stdout.strip()
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("取不到 gh token")


def api(path: str, token: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com{path}")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def git(*args: str, env: dict | None = None) -> str:
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=e)
    return (p.stdout or "").strip() if p.returncode == 0 else f"__ERR__{(p.stderr or '').strip()}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    token = get_token()
    ref = api(f"/repos/{OWNER}/{NAME}/git/ref/heads/main", token)
    remote_sha = ref["object"]["sha"]
    commit = api(f"/repos/{OWNER}/{NAME}/git/commits/{remote_sha}", token)

    print("=" * 78)
    print("  对齐本地 HEAD 与远端 SHA")
    print("=" * 78)
    print(f"  远端 main : {remote_sha[:12]}")
    print(f"  远端 tree : {commit['tree']['sha'][:12]}")
    print(f"  远端提交信息:")
    print(f"    author   : {commit['author']['name']} <{commit['author']['email']}>")
    print(f"    date     : {commit['author']['date']}")
    print(f"    committer: {commit['committer']['name']} <{commit['committer']['email']}>")
    print(f"    message  : {commit['message'].splitlines()[0]}")
    print()

    local_sha = git("rev-parse", "HEAD")
    local_tree = git("rev-parse", "HEAD^{tree}")
    print(f"  本地 HEAD : {local_sha[:12]}")
    print(f"  本地 tree : {local_tree[:12]}")

    if local_sha == remote_sha:
        print("\n  ✅ 已经一致，无需处理")
        return 0
    if local_tree != commit["tree"]["sha"]:
        print("\n  ❌ tree 不同 —— 内容真的不一样，不能用这个方法对齐。")
        print("     请等网络恢复后 git fetch + reset。")
        return 2
    print("\n  ✅ tree 相同，可以本地重放提交对象来对齐 SHA")

    # 父提交（本地应该已有）
    parents = [commit["parents"][i]["sha"] for i in range(len(commit.get("parents", [])))]
    for p in parents:
        if git("cat-file", "-t", p).startswith("__ERR__"):
            print(f"  ❌ 本地缺少父提交对象 {p[:12]}，无法重放（需要 fetch）")
            return 2
    parent_args: list[str] = []
    for p in parents:
        parent_args += ["-p", p]
    print(f"  父提交    : {[p[:8] for p in parents]}（本地都有 ✅）")

    if not args.apply:
        print("\n  —— 检查模式。加 --apply 真正重放并移动本地分支 ——")
        return 0

    # 用 Git 的管道命令重放提交对象，并把作者/提交时间设成与远端一致
    env = {
        "GIT_AUTHOR_NAME": commit["author"]["name"],
        "GIT_AUTHOR_EMAIL": commit["author"]["email"],
        "GIT_AUTHOR_DATE": commit["author"]["date"],
        "GIT_COMMITTER_NAME": commit["committer"]["name"],
        "GIT_COMMITTER_EMAIL": commit["committer"]["email"],
        "GIT_COMMITTER_DATE": commit["committer"]["date"],
    }
    # message 用 stdin 传给 commit-tree（避免命令行引号问题）
    proc = subprocess.run(
        ["git", "-C", str(REPO), "commit-tree", commit["tree"]["sha"], *parent_args],
        input=commit["message"] + "\n",
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, **env},
    )
    if proc.returncode != 0:
        print(f"  ❌ commit-tree 失败: {proc.stderr[:300]}")
        return 2
    new_sha = proc.stdout.strip()
    print(f"  重放出的提交: {new_sha[:12]}")
    print(f"  与远端比对  : " + ("✅ 逐位相同" if new_sha == remote_sha else "❌ 不同（说明还有字段没对齐）"))

    if new_sha != remote_sha:
        print("     不移动本地分支（以免造成不一致）。需要 fetch 对齐。")
        return 2

    # 移动本地 main 与 remote-tracking 引用
    git("update-ref", "refs/heads/main", new_sha)
    git("update-ref", "refs/remotes/origin/main", new_sha)
    print(f"  ✅ refs/heads/main 与 refs/remotes/origin/main 已指向 {new_sha[:12]}")
    print()
    print("=" * 78)
    print("  本地与远端现在完全一致（连 SHA 都相同），且不需要网络 fetch。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
