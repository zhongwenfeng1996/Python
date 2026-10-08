#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目一评估脚本 —— "make eval" 的雏形。

设计原则（这几条决定了它是不是一个**可信**的评估）：

1. **断言必须客观、可复现。** 不用 LLM 打分（LLM-as-judge 不稳定，要多次取均值，
   那是 W7 的事）。这里只做机械可判定的事：关键词命中、JSON 可解析、长度范围、
   是否拒答、是否包含禁用词。**不能自动判定的东西，就不要放进评估集** ——
   放进去只会得到一个你自己都不信的数字。

2. **评估必须能离线跑。** 没有 API Key 时用仓库的 mock 上游跑冒烟集，
   这样 CI 里能跑、面试时能现场演示。真实准确率需要配 Key 跑 `cases.jsonl`。

3. **评估集是资产，代码不是。** 加用例的成本应该接近零，所以用例写在 JSONL 里，
   而不是硬编码在 Python 里。改评估集不需要改代码。

4. **报告要能贴进 README。** 输出同时打印到终端和写 JSON 文件，
   数字直接可用于简历（"字面准确率 X%、P95 延迟 Y ms、单次成本 $Z"）。

用法：
    # 冒烟：用 mock 上游，验证评估框架本身能跑（CI 用这个）
    python eval/run_eval.py --suite smoke

    # 真实评估：需要 backend/.env 里配好 Key
    python eval/run_eval.py --suite full --model deepseek-chat --repeat 3

    # 只看稳定性（同一问题重复跑，看答案是否一致）
    python eval/run_eval.py --suite smoke --repeat 5 --temperature 0.7

退出码：0 = 全部用例通过；1 = 有未通过（可直接用作 CI 门禁）。
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[2]
MOCK = REPO / "ai-lab" / "week01" / "mock_server.py"
EVAL_DIR = PROJECT / "eval"
REPORT_PATH = EVAL_DIR / "report.json"

# 关键词：判断"模型是否拒绝回答/承认不确定"。
# 这是机械匹配，所以故意放得宽 —— 宁可漏判也不要误判成"没拒答"。
HEDGE_WORDS = (
    "不知道", "无法", "不能确定", "不确定", "没有", "无法回答", "无从",
    "抱歉", "没有相关", "查不到", "不存在", "未提供", "缺少", "无法确认",
    "没有信息", "无法得知", "i don't know", "cannot", "unable", "no information",
)


# ----------------------------------------------------------------------
# 启动被测服务（评估必须能一键跑起来，不能要求人先手动开三个终端）
# ----------------------------------------------------------------------
def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port(port: int, proc: subprocess.Popen | None, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        if _port_open(port):
            return True
        time.sleep(0.15)
    return False


def start_stack(app_port: int, mock_port: int,
                jitter: bool = False) -> tuple[subprocess.Popen, subprocess.Popen]:
    log_dir = PROJECT / "logs"
    log_dir.mkdir(exist_ok=True)
    mock_cmd = [sys.executable, str(MOCK), "--port", str(mock_port),
                "--delay", "0.0", "--slow-first-token", "0.02"]
    if jitter:
        # 让 mock 每次都回不同内容 —— 用来验证"稳定性"指标不是哑的
        mock_cmd.append("--jitter")
    mock = subprocess.Popen(mock_cmd,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_port(mock_port, mock):
        raise RuntimeError("mock 上游启动失败")

    import os
    env = dict(os.environ)
    env.update({
        "OPENAI_BASE_URL": f"http://127.0.0.1:{mock_port}/v1",
        "OPENAI_API_KEY": "test",
        "PYTHONUTF8": "1",
    })
    app = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(app_port), "--log-level", "warning"],
        cwd=str(PROJECT / "backend"), env=env,
        stdout=(log_dir / "eval-app.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    if not _wait_port(app_port, app):
        mock.terminate()
        raise RuntimeError("被测应用启动失败（依赖装了吗？跑 setup.ps1）")
    return app, mock


# ----------------------------------------------------------------------
# 断言：每条返回 (是否通过, 人话解释)
# ----------------------------------------------------------------------
def check_answer(answer: str, spec: dict) -> tuple[bool, list[str]]:
    """执行一条用例的断言。返回 (全部通过?, 每条断言的说明)。"""
    notes: list[str] = []
    ok = True
    lowered = answer.lower()

    if "min_chars" in spec:
        good = len(answer) >= spec["min_chars"]
        ok &= good
        notes.append(f"长度 {len(answer)} >= {spec['min_chars']} {'ok' if good else 'FAIL'}")

    if "max_chars" in spec:
        good = len(answer) <= spec["max_chars"]
        ok &= good
        notes.append(f"长度 {len(answer)} <= {spec['max_chars']} {'ok' if good else 'FAIL'}")

    if "keywords" in spec:
        missing = [k for k in spec["keywords"] if k.lower() not in lowered]
        good = not missing
        ok &= good
        notes.append(f"命中关键词 {spec['keywords']} {'ok' if good else f'缺 {missing}'}")

    if "forbidden_keywords" in spec:
        hit = [k for k in spec["forbidden_keywords"] if k.lower() in lowered]
        good = not hit
        ok &= good
        notes.append(f"不含禁用词 {'ok' if good else f'出现 {hit}'}")

    if spec.get("json_valid"):
        # 允许 ```json 围栏，但围栏内的内容必须能解析
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip(),
                          flags=re.MULTILINE)
        try:
            json.loads(stripped)
            notes.append("JSON 可解析 ok")
        except json.JSONDecodeError as exc:
            ok = False
            notes.append(f"JSON 不可解析 FAIL: {exc.msg}")

    if spec.get("refusal_or_hedge"):
        good = any(w in lowered for w in HEDGE_WORDS)
        ok &= good
        notes.append(f"拒答/承认不确定 {'ok' if good else 'FAIL（可能在编造）'}")

    return bool(ok), notes


# ----------------------------------------------------------------------
# 跑评估
# ----------------------------------------------------------------------
def stream_once(client, base_url: str, model: str, question: str,
                temperature: float) -> dict:
    """发一次流式请求，收集文本与 done 事件里的指标。"""
    text_parts: list[str] = []
    done: dict = {}
    started = time.time()
    ttft: float | None = None
    with client.stream(
        "POST", f"{base_url}/api/chat",
        json={"model": model, "temperature": temperature,
              "messages": [{"role": "user", "content": question}]},
    ) as response:
        event = ""
        for line in response.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                if event == "delta":
                    if ttft is None:
                        ttft = time.time() - started
                    try:
                        text_parts.append(json.loads(line[5:].strip())["text"])
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif event == "done":
                    try:
                        done = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        pass
    return {
        "text": "".join(text_parts),
        "ttft_s": ttft if ttft is not None else 0.0,
        "elapsed_s": time.time() - started,
        "done": done,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="项目一评估（make eval 雏形）")
    ap.add_argument("--suite", choices=["smoke", "full"], default="smoke",
                    help="smoke=离线冒烟（mock 上游）；full=真实模型评估")
    ap.add_argument("--cases", type=Path, default=None, help="自定义用例文件")
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--repeat", type=int, default=1,
                    help="每个用例重复次数，>1 时额外报告稳定性")
    ap.add_argument("--app-port", type=int, default=8811)
    ap.add_argument("--mock-port", type=int, default=8812)
    ap.add_argument("--no-serve", action="store_true",
                    help="不自己起服务（服务已在跑）")
    ap.add_argument("--base-url", default="", help="--no-serve 时的应用地址")
    ap.add_argument("--fail-under", type=float, default=None,
                    help="通过率低于该值则以非 0 退出（CI 门禁用）")
    ap.add_argument("--stability-under", type=float, default=None,
                    help="稳定性低于该值则以非 0 退出（需要 --repeat > 1）")
    ap.add_argument("--jitter-mock", action="store_true",
                    help="让 mock 上游每次都回不同内容。**这是给自己做的对照实验**："
                         "开着它跑，稳定性必须掉下来，否则说明该指标写错了")
    args = ap.parse_args()

    cases_path = args.cases or (EVAL_DIR / f"cases.{args.suite}.jsonl"
                                if args.suite == "smoke" else EVAL_DIR / "cases.jsonl")
    if not cases_path.exists():
        print(f"[x] 用例文件不存在：{cases_path}")
        return 1
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if not cases:
        print(f"[x] 用例为空：{cases_path}")
        return 1

    try:
        import httpx
    except ImportError:
        print("[x] 缺少 httpx。先跑 setup.ps1，或 pip install httpx")
        return 1

    print("=" * 78)
    print(f"  项目一评估 · suite={args.suite} · {len(cases)} 条用例 · "
          f"repeat={args.repeat} · temperature={args.temperature}")
    print("=" * 78)
    print(f"  用例文件：{cases_path.relative_to(REPO)}")

    app = mock = None
    base_url = args.base_url
    try:
        if not args.no_serve:
            app, mock = start_stack(args.app_port, args.mock_port,
                                    jitter=args.jitter_mock)
            base_url = f"http://127.0.0.1:{args.app_port}"
            print(f"  被测服务：{base_url}（mock 上游 :{args.mock_port}）")
            if args.jitter_mock:
                print("  ⚠ mock 已开启 jitter：每次回复都不同，稳定性应当明显下降")
                print("    这是对照实验 —— 用来证明稳定性指标不是恒为 100% 的摆设")
            else:
                print("  模式：离线 mock —— 本轮的'通过率'只证明评估框架可用，")
                print("        不代表模型能力。要拿真实数字请 --suite full 并配好 Key。")
        print()

        results: list[dict] = []
        with httpx.Client(timeout=60) as client:
            for case in cases:
                runs = []
                for _ in range(args.repeat):
                    got = stream_once(client, base_url, args.model,
                                      case["question"], args.temperature)
                    passed, notes = check_answer(got["text"], case.get("assert", {}))
                    runs.append({"passed": passed, "notes": notes, **got})
                results.append({"case": case, "runs": runs})

                first = runs[0]
                mark = "PASS" if first["passed"] else "FAIL"
                print(f"[{mark}] {case['id']:<16} "
                      f"ttft={first['ttft_s'] * 1000:>5.0f}ms "
                      f"chars={len(first['text']):>4} | {case.get('note', '')}")
                for note in first["notes"]:
                    print(f"        · {note}")
                if args.repeat > 1:
                    uniq = len({r["text"] for r in runs})
                    print(f"        · {args.repeat} 次运行产生 {uniq} 种不同回答"
                          f"{'（稳定）' if uniq == 1 else '（不稳定）'}")

        # ---------------- 汇总 ----------------
        total_runs = sum(len(r["runs"]) for r in results)
        passed_runs = sum(1 for r in results for run in r["runs"] if run["passed"])
        pass_rate = passed_runs / total_runs * 100 if total_runs else 0.0

        ttfts = sorted(run["ttft_s"] * 1000 for r in results for run in r["runs"])
        elapsed = sorted(run["elapsed_s"] * 1000 for r in results for run in r["runs"])
        costs = [run["done"].get("cost", {}).get("usd", 0.0)
                 for r in results for run in r["runs"] if run["done"]]

        def pct(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            idx = min(int(len(values) * p), len(values) - 1)
            return values[idx]

        # 稳定性：同一用例多次运行是否得到同一答案
        stability = None
        if args.repeat > 1:
            stable = sum(1 for r in results if len({x["text"] for x in r["runs"]}) == 1)
            stability = stable / len(results) * 100

        # 降级次数（应为 0；不为 0 说明有模型在超时）
        degraded = sum(1 for r in results for run in r["runs"]
                       if run["done"].get("degraded"))

        report = {
            "suite": args.suite,
            "model": args.model,
            "temperature": args.temperature,
            "repeat": args.repeat,
            "cases": len(cases),
            "runs": total_runs,
            "pass_rate_pct": round(pass_rate, 1),
            "stability_pct": None if stability is None else round(stability, 1),
            "ttft_ms": {"p50": round(pct(ttfts, 0.5), 1),
                        "p95": round(pct(ttfts, 0.95), 1),
                        "max": round(ttfts[-1], 1) if ttfts else 0.0},
            "elapsed_ms": {"p50": round(pct(elapsed, 0.5), 1),
                           "p95": round(pct(elapsed, 0.95), 1)},
            "cost_usd": {"mean": round(statistics.fmean(costs), 8) if costs else 0.0,
                         "total": round(sum(costs), 6)},
            "degraded_runs": degraded,
            "failed_ids": [r["case"]["id"] for r in results
                           if not all(x["passed"] for x in r["runs"])],
        }
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")

        print()
        print("=" * 78)
        print("  评估报告")
        print("=" * 78)
        print(f"  用例数 / 运行次数      {report['cases']} / {report['runs']}")
        print(f"  断言通过率             {report['pass_rate_pct']}%"
              f"   （{passed_runs}/{total_runs}）")
        if stability is not None:
            print(f"  答案稳定性             {report['stability_pct']}%"
                  f"   （{args.repeat} 次运行答案完全一致的用例占比）")
        print(f"  TTFT  p50 / p95        {report['ttft_ms']['p50']} / "
              f"{report['ttft_ms']['p95']} ms")
        print(f"  端到端 p50 / p95       {report['elapsed_ms']['p50']} / "
              f"{report['elapsed_ms']['p95']} ms")
        print(f"  单次成本 均值 / 合计   ${report['cost_usd']['mean']} / "
              f"${report['cost_usd']['total']}")
        print(f"  降级次数               {report['degraded_runs']}")
        if report["failed_ids"]:
            print(f"  未通过用例             {', '.join(report['failed_ids'])}")
        print("=" * 78)
        print(f"  报告已写入：{REPORT_PATH.relative_to(REPO)}")

        if args.repeat == 1 and stability is None:
            print("  提示：加 --repeat 3 可以测答案稳定性（面试第一题就问这个）")

        if args.fail_under is not None and pass_rate < args.fail_under:
            print(f"\n[FAIL] 通过率 {pass_rate:.1f}% 低于门禁 {args.fail_under}%")
            return 1
        if args.stability_under is not None:
            if stability is None:
                print("\n[FAIL] --stability-under 需要 --repeat > 1 才有意义")
                return 1
            if stability < args.stability_under:
                print(f"\n[FAIL] 稳定性 {stability:.1f}% 低于门禁 "
                      f"{args.stability_under}%")
                return 1
            print(f"\n[PASS] 稳定性 {stability:.1f}% 不低于门禁 "
                  f"{args.stability_under}%")
        return 0
    finally:
        for proc in (app, mock):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
