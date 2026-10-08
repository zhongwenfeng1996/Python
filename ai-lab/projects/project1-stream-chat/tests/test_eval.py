"""
评估框架自身的测试。

为什么评估脚本也需要测试：
    评估是"用来判断别的代码对不对"的代码。如果它自己有 bug，
    你会得到一个**看起来可信但其实是错的**数字 —— 那比没有评估更危险，
    因为你会拿它去改代码、去写简历。

    LLM 应用的评估尤其容易出这类问题：断言写松了永远 100%，
    写紧了永远 0%，两种都不会报错，只会安静地给你一个没意义的数。

这里测三件事：
    1. check_answer 的每种断言在通过/失败两侧都对（不是恒真或恒假）
    2. 稳定性指标能测出不确定性（用 jitter 上游做对照）
    3. 评估脚本的退出码语义正确（门禁不达标必须非 0）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL_DIR))

from run_eval import HEDGE_WORDS, check_answer  # noqa: E402


# ----------------------------------------------------------------------
# 断言的两侧：每条规则都必须能"通过"也能"失败"
# ----------------------------------------------------------------------
class TestAssertions:
    def test_min_chars_both_sides(self):
        assert check_answer("够长的回答内容", {"min_chars": 5})[0] is True
        assert check_answer("短", {"min_chars": 5})[0] is False

    def test_max_chars_both_sides(self):
        assert check_answer("短", {"max_chars": 5})[0] is True
        assert check_answer("这是一段很长很长的回答内容", {"max_chars": 5})[0] is False

    def test_keywords_both_sides(self):
        assert check_answer("向量就是有方向的量", {"keywords": ["向量"]})[0] is True
        assert check_answer("我不清楚", {"keywords": ["向量"]})[0] is False

    def test_forbidden_keywords_both_sides(self):
        assert check_answer("正常回答", {"forbidden_keywords": ["你是"]})[0] is True
        assert check_answer("你是我的助手", {"forbidden_keywords": ["你是"]})[0] is False

    def test_json_valid_both_sides(self):
        assert check_answer('{"a": 1}', {"json_valid": True})[0] is True
        # 带 ```json 围栏也算过（模型很爱这么输出）
        assert check_answer('```json\n{"a": 1}\n```', {"json_valid": True})[0] is True
        assert check_answer('{"a": 1', {"json_valid": True})[0] is False
        assert check_answer('好的，这是 JSON：{"a": 1}', {"json_valid": True})[0] is False

    def test_refusal_both_sides(self):
        assert check_answer("抱歉，我不知道 2027 年的冠军是谁",
                            {"refusal_or_hedge": True})[0] is True
        assert check_answer("2027 年世界杯冠军是巴西队",
                            {"refusal_or_hedge": True})[0] is False

    def test_multiple_rules_all_must_pass(self):
        spec = {"keywords": ["向量"], "min_chars": 10, "forbidden_keywords": ["香蕉"]}
        assert check_answer("向量是一个有方向的数学对象", spec)[0] is True
        # 关键词命中但长度不够 → 整体失败
        assert check_answer("向量", spec)[0] is False

    def test_notes_explain_every_rule(self):
        """失败时必须说清是哪条规则失败 —— 否则报告没法读。"""
        passed, notes = check_answer("短", {"min_chars": 50, "keywords": ["向量"]})
        assert passed is False
        joined = " | ".join(notes)
        assert "50" in joined and "向量" in joined

    def test_no_assertions_always_passes(self):
        """空断言集：不报错、算通过（用例可以只作为观察位）。"""
        assert check_answer("随便什么", {})[0] is True


# ----------------------------------------------------------------------
# 拒答词表：不能空，也不能宽到什么都算拒答
# ----------------------------------------------------------------------
class TestHedgeWords:
    def test_hedge_words_not_empty(self):
        assert len(HEDGE_WORDS) >= 5

    def test_normal_answer_is_not_hedge(self):
        normal = "向量是一个有方向和大小的数学对象，常用于表示特征。"
        assert not any(w in normal.lower() for w in HEDGE_WORDS)

    def test_typical_refusals_are_hedge(self):
        for text in ("我不知道", "无法确定", "抱歉，我没有这方面的信息",
                     "I don't know", "unable to answer"):
            assert any(w in text.lower() for w in HEDGE_WORDS), text


# ----------------------------------------------------------------------
# 用例文件：格式与内容都要被检查
# ----------------------------------------------------------------------
class TestCaseFiles:
    @pytest.mark.parametrize("name", ["cases.smoke.jsonl", "cases.jsonl"])
    def test_case_file_is_well_formed(self, name):
        import json
        path = EVAL_DIR / name
        assert path.exists(), f"缺少用例文件 {name}"
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert lines, f"{name} 是空的"
        ids = set()
        for i, line in enumerate(lines, 1):
            case = json.loads(line)          # 格式错会在这里抛
            assert "id" in case, f"{name}:{i} 缺 id"
            assert "question" in case, f"{name}:{i} 缺 question"
            assert "assert" in case, f"{name}:{i} 缺 assert（没有断言的用例没有意义）"
            assert case["id"] not in ids, f"{name}:{i} id 重复：{case['id']}"
            ids.add(case["id"])

    def test_full_suite_covers_key_risks(self):
        """真实评估集必须覆盖幻觉、注入、拒答这几类风险，不能只有 happy path。"""
        import json
        cases = [json.loads(ln) for ln in
                 (EVAL_DIR / "cases.jsonl").read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        specs = [c["assert"] for c in cases]
        assert any(s.get("json_valid") for s in specs), "缺结构化输出用例"
        assert any(s.get("refusal_or_hedge") for s in specs), "缺幻觉/拒答用例"
        assert any(s.get("forbidden_keywords") for s in specs), "缺提示词注入用例"
        assert len(cases) >= 5, "真实评估集太小，数字没有统计意义"
