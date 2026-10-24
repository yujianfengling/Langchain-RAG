"""Citation/Faithfulness 门禁测试（2026-10-24）

锁定：引用编号必须真实存在；没有引用/引用与回答无关时门禁失败。
这是词面代理指标，不冒充语义级 LLM 评判。
"""

import importlib.util
import json
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent / "eval"
GATE_PATH = EVAL_DIR / "faithfulness_gate.py"
BASELINE_PATH = EVAL_DIR / "faithfulness_baseline.json"

_spec = importlib.util.spec_from_file_location("faithfulness_gate", GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(gate)


def test_sample_passes_gate_and_baseline():
    result = gate.run_gate()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    checked = gate.check_gate(result, baseline=baseline)
    assert checked["ok"], checked["failures"]
    assert result["metrics"]["citation_validity"] == 1.0


def test_invalid_citation_fails():
    result = {"metrics": {"citation_validity": 0.5, "citation_rate": 1.0,
                          "faithfulness": 0.5, "lexical_support": 0.1}}
    checked = gate.check_gate(result)
    assert checked["ok"] is False
    assert any("citation_validity" in item for item in checked["failures"])


def test_missing_citation_fails():
    result = {"metrics": {"citation_validity": 1.0, "citation_rate": 0.2,
                          "faithfulness": 0.2, "lexical_support": 0.0}}
    checked = gate.check_gate(result)
    assert checked["ok"] is False
    assert any("citation_rate" in item for item in checked["failures"])


def test_gate_detects_regression_against_baseline():
    current = {"metrics": {"citation_validity": 1.0, "citation_rate": 1.0,
                           "faithfulness": 0.80, "lexical_support": 0.5}}
    baseline = {"metrics": {"citation_validity": 1.0, "citation_rate": 1.0,
                            "faithfulness": 1.0, "lexical_support": 0.9}}
    checked = gate.check_gate(current, baseline=baseline, max_drop=0.05)
    assert checked["ok"] is False
    assert any("低于基线" in item for item in checked["failures"])


def test_case_metrics_flags_unrelated_answer():
    case = {
        "id": "bad",
        "answer": "今天天气很好，适合出去玩。",
        "sources": [{"id": 1, "text": "二叉树的前序遍历顺序是根左右。"}],
        "cited_ids": [1],
    }
    row = gate._case_metrics(case)
    assert row["citation_valid"] is True
    assert row["supported"] is False
