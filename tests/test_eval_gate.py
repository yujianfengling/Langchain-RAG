"""评测门禁回归测试（2026-10-24）

锁定三件事：
1. 黄金集结构完整（每题都有期望资料，且资料真实存在）；
2. 当前 BM25 检索不低于基线/阈值；
3. 指标下降时门禁必须失败（而不是只打印报告）。
"""

import importlib.util
import json
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent / "eval"
GATE_PATH = EVAL_DIR / "golden_gate.py"
BASELINE_PATH = EVAL_DIR / "baseline.json"

_spec = importlib.util.spec_from_file_location("golden_gate", GATE_PATH)
gate = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(gate)


def test_golden_set_is_complete():
    cases = gate.load_golden()
    sources = {p.name for p in (EVAL_DIR / "dataset" / "materials").glob("*.txt")}
    assert len(cases) >= 20
    for case in cases:
        assert case.get("id"), case
        assert case.get("question"), case
        assert case.get("expected_source") in sources, case
        assert case.get("keyword"), case


def test_current_retrieval_passes_gate_and_baseline():
    result = gate.run_golden()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    checked = gate.check_gate(result, baseline=baseline)
    assert checked["ok"], checked["failures"]
    assert result["metrics"]["recall_at_5"] >= 0.95


def test_gate_rejects_low_metrics():
    low = {"metrics": {"recall_at_5": 0.5, "hit_at_1": 0.4,
                       "keyword_hit_at_1": 0.4, "mrr": 0.4, "pass_rate": 0.4}}
    checked = gate.check_gate(low)
    assert checked["ok"] is False
    assert any("recall_at_5" in item for item in checked["failures"])


def test_gate_detects_regression_against_baseline():
    current = {"metrics": {"recall_at_5": 0.90, "hit_at_1": 0.95,
                           "keyword_hit_at_1": 0.95, "mrr": 0.95, "pass_rate": 0.95}}
    baseline = {"metrics": {"recall_at_5": 1.0, "hit_at_1": 0.95,
                            "keyword_hit_at_1": 0.95, "mrr": 0.95, "pass_rate": 0.95}}
    checked = gate.check_gate(current, baseline=baseline, max_drop=0.05)
    assert checked["ok"] is False
    assert any("低于基线" in item for item in checked["failures"])


def test_cli_exit_code_reflects_gate(monkeypatch, capsys):
    monkeypatch.setattr(gate, "run_golden", lambda: {
        "metrics": {"recall_at_5": 0.1, "hit_at_1": 0.1,
                    "keyword_hit_at_1": 0.1, "mrr": 0.1, "pass_rate": 0.1},
        "cases": [],
    })
    code = gate.main(["--baseline", "__none__"])
    assert code == 1
