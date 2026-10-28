"""Faithfulness / 引用落地门禁（2026-10-24）

只做**可复现的引用审计**，不冒充语义级 LLM 评判：
- Citation Validity：回答引用的 [n] 必须真实存在于本轮 sources；
- Citation Rate：有引用的回答占比；
- Lexical Support：引用片段与回答的 token 覆盖率（代理指标，阈值保守）；
- Faithfulness：引用有效且至少一个引用片段能词面支撑回答的占比。

输入 JSON：
{
  "cases": [
    {"id": "c1", "question": "...", "answer": "...",
     "sources": [{"id": 1, "text": "..."}], "cited_ids": [1]}
  ]
}

局限：词面覆盖不能证明语义忠实；它只防“引用编号造假 / 回答与原文完全无关”。
真正的 LLM-as-judge 可在后续加 --llm，但默认 CI 不调用模型。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = EVAL_DIR / "faithfulness_sample.json"
DEFAULT_BASELINE = EVAL_DIR / "faithfulness_baseline.json"

DEFAULT_THRESHOLDS = {
    "citation_validity": 1.0,
    "citation_rate": 0.8,
    "faithfulness": 0.8,
}
SUPPORT_THRESHOLD = 0.25
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    if not text:
        return []
    try:
        import jieba
        raw = jieba.lcut(text)
    except Exception:
        raw = _TOKEN_RE.findall(text)
    return [t.strip() for t in raw if t.strip() and not re.fullmatch(r"[\W_]+", t.strip())]


def _case_metrics(case: Dict[str, Any]) -> Dict[str, Any]:
    sources = {}
    for src in case.get("sources") or []:
        try:
            sources[int(src.get("id"))] = str(src.get("text") or "")
        except (TypeError, ValueError):
            continue
    cited = []
    for value in case.get("cited_ids") or []:
        try:
            cited.append(int(value))
        except (TypeError, ValueError):
            continue
    valid = all(cid in sources for cid in cited) if cited else True
    answer_tokens = set(tokenize(str(case.get("answer") or "")))
    best = 0.0
    for cid in cited:
        source_tokens = set(tokenize(sources.get(cid, "")))
        if not answer_tokens or not source_tokens:
            continue
        best = max(best, len(answer_tokens & source_tokens) / max(1, len(answer_tokens)))
    supported = bool(valid and cited and best >= SUPPORT_THRESHOLD)
    return {
        "id": case.get("id"),
        "cited_ids": cited,
        "citation_valid": bool(valid),
        "has_citation": bool(cited),
        "lexical_support": round(best, 4),
        "supported": supported,
    }


def run_gate(path: Path = DEFAULT_INPUT) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError("Faithfulness 输入为空或格式不正确")
    rows = [_case_metrics(case) for case in cases]
    total = len(rows)
    cited_rows = [row for row in rows if row["has_citation"]]
    metrics = {
        "sample_count": total,
        "citation_validity": round(sum(1 for row in rows if row["citation_valid"]) / total, 4),
        "citation_rate": round(len(cited_rows) / total, 4),
        "lexical_support": round(
            sum(row["lexical_support"] for row in cited_rows) / max(1, len(cited_rows)), 4),
        "faithfulness": round(sum(1 for row in rows if row["supported"]) / total, 4),
        "pass_rate": round(sum(1 for row in rows if row["supported"]) / total, 4),
    }
    return {"metrics": metrics, "cases": rows}


def check_gate(result: Dict[str, Any],
               thresholds: Optional[Dict[str, float]] = None,
               baseline: Optional[Dict[str, Any]] = None,
               max_drop: float = 0.05) -> Dict[str, Any]:
    metrics = result.get("metrics") or {}
    limits = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        limits.update(thresholds)
    failures: List[str] = []
    for name, minimum in limits.items():
        actual = metrics.get(name)
        if actual is None:
            failures.append(f"{name} 缺失（要求 >= {minimum}）")
        elif float(actual) < float(minimum):
            failures.append(f"{name}={actual} < {minimum}")
    if baseline:
        for name, base_value in (baseline.get("metrics") or {}).items():
            if name not in metrics:
                continue
            if float(metrics[name]) < float(base_value) - max_drop:
                failures.append(f"{name}={metrics[name]} 低于基线 {base_value} 超过 {max_drop}")
    return {"ok": not failures, "failures": failures, "metrics": metrics,
            "thresholds": limits}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Citation/Faithfulness 门禁")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                        help="基线 JSON；传 __none__ 可禁用")
    parser.add_argument("--json", default="")
    parser.add_argument("--max-drop", type=float, default=0.05)
    args = parser.parse_args(argv)
    result = run_gate(Path(args.input))
    baseline = None
    if args.baseline and Path(args.baseline).is_file():
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    gate = check_gate(result, baseline=baseline, max_drop=args.max_drop)
    text = json.dumps({"result": result, "gate": gate}, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if gate["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
