#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断：为什么本地重放的提交 SHA 与远端不一致

思路：固定 tree / parent / author / committer 的姓名邮箱时间，
只变 **message 的字节**（Git 对象里 message 是逐字节参与的），
看哪个变体能算出远端那个 SHA。

如果都不匹配，说明还有别的字段差异（比如 GitHub 用了不同的 committer
时区表示），那就只能用 fetch 对齐 —— 但至少这个实验能给出结论。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = r"G:\转型"
GH = r"C:\Program Files\GitHub CLI\gh.exe"


def token() -> str:
    return subprocess.run([GH, "auth", "token"], capture_output=True,
                          text=True).stdout.strip()


def api(path: str, tok: str) -> dict:
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    tok = token()
    ref = api("/repos/zhongwenfeng1996/Python/git/ref/heads/main", tok)
    sha = ref["object"]["sha"]
    c = api(f"/repos/zhongwenfeng1996/Python/git/commits/{sha}", tok)

    print("=" * 74)
    print("  诊断：重放提交的 SHA 差异来源")
    print("=" * 74)
    print(f"  目标（远端）SHA : {sha}")
    print(f"  tree            : {c['tree']['sha']}")
    print(f"  parents         : {[p['sha'][:8] for p in c['parents']]}")
    print(f"  author          : {c['author']['name']} <{c['author']['email']}>")
    print(f"  author date     : {c['author']['date']}")
    print(f"  committer       : {c['committer']['name']} <{c['committer']['email']}>")
    print(f"  committer date  : {c['committer']['date']}")

    msg = c["message"]
    print(f"  message 长度    : {len(msg)} 字符")
    print(f"  message 结尾40字符 repr: {msg[-40:]!r}")
    print()

    env = {
        "GIT_AUTHOR_NAME": c["author"]["name"],
        "GIT_AUTHOR_EMAIL": c["author"]["email"],
        "GIT_AUTHOR_DATE": c["author"]["date"],
        "GIT_COMMITTER_NAME": c["committer"]["name"],
        "GIT_COMMITTER_EMAIL": c["committer"]["email"],
        "GIT_COMMITTER_DATE": c["committer"]["date"],
    }
    parent_args: list[str] = []
    for p in c["parents"]:
        parent_args += ["-p", p["sha"]]

    base = ["git", "-C", REPO, "commit-tree", c["tree"]["sha"], *parent_args]

    # Git 的 message 处理：commit-tree 读到 EOF，末尾单个换行会被规范掉。
    # 所以试几种「message 尾部换行数」的变体。
    variants: dict[str, str] = {
        "strip()": msg.strip(),
        "strip()+\\n": msg.strip() + "\n",
        "strip()+\\n\\n": msg.strip() + "\n\n",
        "raw": msg,
        "raw 去掉末尾所有换行": msg.rstrip("\n"),
    }

    matched = False
    for label, body in variants.items():
        p = subprocess.run(base, input=body, capture_output=True, text=True,
                           encoding="utf-8", env={**os.environ, **env})
        got = (p.stdout or "").strip()
        ok = got == sha
        if ok:
            matched = True
        print(f"  {label:<22} -> {got[:12]}  {'✅ 匹配' if ok else '✗'}")

    print()
    print("=" * 74)
    if matched:
        print("  找到了匹配的 message 形式 —— 本地可以精确重放远端提交")
    else:
        print("  没有变体能匹配。说明差异不在 message 的换行上，")
        print("  而在 commit 对象的其它字段（GitHub 生成提交的方式与本地不同）。")
        print()
        print("  结论：**只能靠 fetch 对齐**。但内容已确认一致（tree 相同），")
        print("        所以这纯粹是'元数据不同'的问题，不影响任何实际内容。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
