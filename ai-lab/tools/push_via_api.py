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


def sync_stale_files(token: str, label: str = "") -> int:
    """
    对比本地 HEAD 与远端 main 的每个文件 blob，把**不一致的逐个补齐**。

    返回补齐的文件数。

    ## 为什么必须在推送**之前**调，而且必须独立于推送

    本脚本的推送逻辑是"按本提交相对远端的差异"发文件的。
    但本地与远端的 SHA **长期分叉**（每次 API 推送都会生成一个
    GitHub 自己的 commit 对象），分叉累积后这个"哪些文件变了"
    的判定会**漏项** —— 文件被静默跳过、远端停在旧版，
    而脚本报"推送完成"。

    实测抓到过：一次推送漏了 3 个文件（ADR.md / README.md / main.py，
    内容差 1000+ 字符，远端**完全没有** `warm_cache` 这个修复）。

    更麻烦的是它**自我循环**：修这个脚本本身要靠这个脚本来推。
    如果只在"有东西要推"的分支里自检，那么当远端被判定为
    "本地子集"（不需要推）时就跳过了自检，问题永远修不掉。

    所以：自检必须是**独立的一步**，无论走哪条分支都要跑。
    """
    local_blobs = _local_blobs()
    remote_blobs = _remote_blobs(token)
    missing = sorted(set(local_blobs) - set(remote_blobs))
    differing = sorted(p for p in set(local_blobs) & set(remote_blobs)
                       if local_blobs[p] != remote_blobs[p])
    stale = missing + differing

    prefix = f"  [{label}] " if label else "  "
    if not stale:
        print(f"{prefix}✅ 本地与远端一致（{len(local_blobs)} 个文件的 blob 全同）")
        return 0

    print(f"{prefix}⚠️ 发现 {len(stale)} 个文件与远端不一致（推送漏项）：")
    for p in stale:
        print(f"{prefix}    [{'远端缺失' if p in missing else '内容不同'}] {p}")
    print(f"{prefix}逐个按 **git 索引内容**（不是磁盘内容）强制同步：")
    n = 0
    for p in stale:
        raw = subprocess.run(
            ["git", "-C", str(REPO), "show", f"HEAD:{p}"],
            capture_output=True).stdout
        if not raw:
            print(f"{prefix}    [跳过] {p}（git 里取不到）")
            continue
        # 建 blob
        blob = api("POST", f"/repos/{OWNER}/{NAME}/git/blobs", token,
                   {"content": base64.b64encode(raw).decode("ascii"),
                    "encoding": "base64"})
        # 基于远端当前 tree 建新 tree（只改这一个文件）
        ref = api("GET", f"/repos/{OWNER}/{NAME}/git/ref/heads/main", token)
        parent = ref["object"]["sha"]
        pcommit = api("GET", f"/repos/{OWNER}/{NAME}/git/commits/{parent}", token)
        ntree = api("POST", f"/repos/{OWNER}/{NAME}/git/trees", token,
                    {"base_tree": pcommit["tree"]["sha"],
                     "tree": [{"path": p, "mode": "100644",
                               "type": "blob", "sha": blob["sha"]}]})
        nc = api("POST", f"/repos/{OWNER}/{NAME}/git/commits", token,
                 {"message": f"fix(sync): 补上 API 推送漏掉的 {p}",
                  "tree": ntree["sha"], "parents": [parent]})
        api("PATCH", f"/repos/{OWNER}/{NAME}/git/refs/heads/main", token,
            {"sha": nc["sha"], "force": False})
        print(f"{prefix}    ✅ {p}  -> {nc['sha'][:8]}")
        n += 1
    return n


def _local_blobs() -> dict[str, str]:
    """
    本地 HEAD 的 {路径: blob sha}。

    抽成函数是为了让"推送前的安全检查"和"推送后的自检"**用同一份逻辑** ——
    这个脚本上我已经因为"同一判断两处实现"吃过亏
    （`remote_is_subset_of_local` 里内联了一份，自检又写一份，迟早漂移）。
    """
    out: dict[str, str] = {}
    for line in git("ls-tree", "-r", "HEAD").splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            out[path] = parts[2]
    return out


def _remote_blobs(token: str) -> dict[str, str]:
    """远端 main 的 {路径: blob sha}（走 trees API ?recursive=1）。"""
    ref = api("GET", f"/repos/{OWNER}/{NAME}/git/ref/heads/main", token)
    commit = api("GET", f"/repos/{OWNER}/{NAME}/git/commits/{ref['object']['sha']}",
                 token)
    tree = api("GET",
               f"/repos/{OWNER}/{NAME}/git/trees/{commit['tree']['sha']}?recursive=1",
               token)
    return {e["path"]: e["sha"] for e in tree["tree"] if e["type"] == "blob"}


def remote_is_subset_of_local(remote_sha: str, token: str,
                              max_commits: int = 60) -> tuple[bool, list[str]]:
    """
    判断远端的内容是否已被本地完全包含（决定 force 覆盖是否安全）。

    危险只有一种：**远端有一个文件，本地没有**（force 会让它消失）。
    所以只需要比**文件集合与内容**：

        remote tree 里的每个文件，都能在 local tree 里找到、且 blob 相同

    满足 ⇒ 远端的任何内容都已在本地 ⇒ force 不丢东西。

    ⚠️ 这里踩过一个真 bug：第一版沿远端历史逐个 commit 做
    git diff <parent> <tree>。但 force 覆盖会把被丢弃的 commit 对象
    从本地清掉（不再被任何 ref 引用 → 最终 gc），于是 git diff 报
    atal: bad object，检查直接崩。
    **改成比文件集合就绕开了这个依赖** —— 不需要那些 commit 对象存在。

    远端文件列表走 API 递归取（远端可能用了本地没有的 tree 对象，
    不能靠 git 读远端 tree）。
    """
    lines: list[str] = []

    # 本地文件 -> blob
    ls = git("ls-tree", "-r", "HEAD")
    local_blobs: dict[str, str] = {}
    for line in ls.splitlines():
        if "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3:
            local_blobs[path] = parts[2]

    # 远端文件 -> blob
    info = api("GET", f"/repos/{OWNER}/{NAME}/git/commits/{remote_sha}", token)
    remote_tree = info["tree"]["sha"]
    tree_data = api("GET",
                    f"/repos/{OWNER}/{NAME}/git/trees/{remote_tree}?recursive=1",
                    token)
    remote_blobs: dict[str, str] = {}
    for item in tree_data.get("tree", []):
        if item.get("type") == "blob":
            remote_blobs[item["path"]] = item["sha"]

    lines.append(f"远端文件数 {len(remote_blobs)}   本地文件数 {len(local_blobs)}")

    missing = [p for p in remote_blobs if p not in local_blobs]
    differing = [p for p, sha in remote_blobs.items()
                 if p in local_blobs and local_blobs[p] != sha]

    if missing:
        lines.append(f"⚠️ 远端有 {len(missing)} 个文件本地没有（force 会丢掉）：")
        lines += [f"   {p}" for p in sorted(missing)[:10]]
    if differing:
        lines.append(f"⚠️ {len(differing)} 个文件内容不同（本地版本会生效）：")
        lines += [f"   {p}" for p in sorted(differing)[:10]]
    if not missing and not differing:
        lines.append("✅ 远端每个文件都能在本地找到，且内容相同")

    return (not missing and not differing), lines


def _normalize_eol(raw: bytes, path: str) -> tuple[bytes, str]:
    """
    按 git 的 eol=lf 规则把 CRLF 归一成 LF。返回 (内容, 备注)。

    ## 为什么必须做（这个 bug 很隐蔽）

    `.gitattributes` 规定 `* text=auto eol=lf`，所以 **git 索引里永远是 LF**。
    但**磁盘工作区可能是 CRLF** —— 而 `git add` 会自动归一化，
    于是 `git status` 是干净的：**索引 LF、磁盘 CRLF，和平共处，看不出来**。

    这个脚本却是直接 `read_bytes()` 发给 GitHub 的 → 远端存成 CRLF。
    实测：README 本地 18188 字符/0 个 CRLF，远端 18788 字符/600 个 CRLF。
    内容一模一样，只是 blob 哈希不同 → 报"本地与远端 tree 不一致"，
    而且 **git 永远不会去修它**（因为 git 认为本地是干净的）。

    ## 哪些文件不能碰

    二进制文件绝对不能做行尾替换 —— 图片、SQLite 库、zip 里的
    0x0D0A 序列会被破坏。判据：
      · 扩展名在白名单里（明确的文本类型），或
      · 内容里不含 NUL 字节（二进制通常含）
    """
    # 明确的二进制扩展名 —— 直接放过
    binary_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
                  ".zip", ".gz", ".whl", ".exe", ".dll", ".so", ".dylib",
                  ".db", ".sqlite3", ".woff", ".woff2", ".ttf", ".xlsx",
                  ".docx", ".pptx", ".mp4", ".mp3", ".bin", ".esd", ".wim"}
    ext = Path(path).suffix.lower()
    if ext in binary_ext:
        return raw, ""

    # 含 NUL 字节 → 当二进制处理（文本文件不会有 NUL）
    if b"\x00" in raw:
        return raw, ""

    if b"\r\n" not in raw:
        return raw, ""

    n_crlf = raw.count(b"\r\n")
    fixed = raw.replace(b"\r\n", b"\n")
    return fixed, f", CRLF {n_crlf}→0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正推送（默认只预览）")
    ap.add_argument("--commit", default="HEAD", help="要推送的本地提交")
    ap.add_argument(
        "--allow-overwrite", action="store_true",
        help="远端有本地缺失/内容不同的文件时也继续（force）。"
             "用于**你自己的**历史分叉（例如本地刚删了几个文件、"
             "或本地改进了某个文件）。会对远端做 force 更新 —— "
             "如果远端可能有别人的提交，不要加这个参数。")
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

    # ================================================================
    # 先补齐"推送漏项"，再做常规推送
    # ================================================================
    #
    # 顺序很重要：先把远端补齐到与本地一致，后面的推送才会有正确的基准树。
    # 而且这一步必须**独立于**后面的分支 —— 否则当远端被判定为
    # "本地子集、无需推送"时会跳过自检，漏项就永远修不掉（实测踩到过）。
    if args.apply:
        n_synced = sync_stale_files(token, label="推送前自检")
        if n_synced:
            print(f"  → 已补齐 {n_synced} 个文件，重新读取远端状态")
            ref = api("GET", f"/repos/{OWNER}/{NAME}/git/ref/heads/main", token)
            remote_sha = ref["object"]["sha"]
            print(f"  远端 main: {remote_sha[:8]}")
        print()

    # 安全检查：远端的提交必须是本地的祖先（否则会覆盖别人的提交）
    is_ancestor = subprocess.run(
        ["git", "-C", str(REPO), "merge-base", "--is-ancestor", remote_sha, sha_local],
        capture_output=True)
    need_override = False
    if is_ancestor.returncode != 0:
        # 远端不是本地祖先。**不要立刻拒绝** —— 先判断是不是"内容等价的历史分叉"。
        #
        # 为什么需要这个判断（实测场景）：
        #   用本脚本推送一次后，远端多了一个 GitHub 生成的提交；
        #   如果本地在那之后再 amend（比如修提交消息里的 BOM），
        #   本地与远端就在"内容相同"的前提下分叉了：
        #       tree 一样，但 commit 不是彼此的祖先
        #   这时既不能快进，也不该盲目拒绝 —— 需要看**内容是否等价**。
        # 正确判据：**远端的每一次变更是否都已体现在本地**。
        # 沿远端历史往回走，逐个提交取它相对父提交的变更文件，
        # 检查这些文件在本地 HEAD 里存在且 blob 相同。
        # 全部满足 → 远端没有本地缺失的内容 → force 安全。
        print("  ⚠️ 远端不是本地祖先 —— 检查它是否已被本地完全包含…")
        subset_ok, detail = remote_is_subset_of_local(remote_sha, token)
        for line in detail:
            print(f"     {line}")
        if subset_ok:
            need_override = True
            print("     → 远端变更都已在本地体现，将用 force 更新（内容不丢）")
        elif args.allow_overwrite:
            # 明确要求覆盖。用于"本地主动删了文件 / 本地改进了文件"这类
            # **自己的**历史分叉 —— 上面列出的差异正是我们想要的差异。
            need_override = True
            print("     ⚠️ 上面这些差异按 --allow-overwrite 处理：")
            print("         · 远端有而本地无的文件 → 会被删除（这是本地的意图）")
            print("         · 内容不同的文件 → 采用本地版本")
            print("     → 将对远端做 force 更新")
        else:
            print("  ❌ 远端有本地缺失或不同的内容 —— 拒绝覆盖。")
            print("     如果你确认这是**你自己的**历史分叉（如刚删了文件、")
            print("     或本地改进了某文件），加 --allow-overwrite 重试。")
            print("     如果远端可能有别人的提交，请不要加，先 git fetch 合并。")
            return 2
    if not need_override:
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

        # ============================================================
        # 行尾符归一化 —— 必须做，否则 API 推送会与 git 索引不一致
        # ============================================================
        #
        # 【踩过的坑】本仓库的 .gitattributes 规定 `* text=auto eol=lf`，
        # 所以 git 索引里存的永远是 LF。但**磁盘上的工作区文件可能是 CRLF**
        # （我用 Python 写文件时默认就是 CRLF，编辑器也可能加）。
        #
        # 而 `git add` 时会自动归一化，所以 `git status` 是干净的 ——
        # **索引里是 LF，磁盘上是 CRLF，两者共存且看不出问题**。
        #
        # 但这个脚本是直接把 `read_bytes()` 发给 GitHub 的，
        # 于是远端存的就是 CRLF。实测抓到：
        #     ai-lab/projects/project2-rag/README.md
        #     本地 git 18188 字符 / 0 个 CRLF
        #     远端 API 18788 字符 / 600 个 CRLF
        # 内容一模一样，但 blob 哈希不同 → "本地与远端 tree 不一致"，
        # 而且这个差异会**一直存在**，因为 git 认为本地是干净的、
        # 根本不会去推这个文件。
        #
        # 修法：对文本文件按 git 的规则把 CRLF 归一成 LF。
        # 二进制文件（图片等）绝对不能碰，所以要先判类型。
        raw, note = _normalize_eol(raw, path)

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
        print(f"    [{status}] {path}  ({len(raw)} 字节, mode={mode}{note})")

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
    # force 只在"内容等价的历史分叉"时才置 True（见上面的安全检查）。
    # 有 force 意味着**丢弃远端的那个提交对象**（但它的内容已在我们的 tree 里，
    # 所以不会丢失任何文件内容）。
    api("PATCH", f"/repos/{OWNER}/{NAME}/git/refs/heads/main", token,
        {"sha": commit["sha"], "force": bool(need_override)})
    print(f"  ✅ refs/heads/main -> {commit['sha'][:8]}"
          f"{'（force）' if need_override else ''}")
    print()

    # 推送后再核一次 —— 确认这次推送真的把内容送上去了
    # （上面"推送前自检"保证了基准是对的，这里是防止推送本身又引入漏项）
    print("=" * 78)
    if sync_stale_files(token, label="推送后核验"):
        print("  （本轮的漏项已补齐 —— 说明推送逻辑仍不可靠，值得查）")

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
