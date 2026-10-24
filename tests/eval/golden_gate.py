"""检索黄金集门禁（2026-10-24）

只跑**检索**，不调模型：用 BM25 在测试资料上排序，计算
- Recall@5：期望资料是否进前 5；
- Hit@1：期望资料是否排第 1；
- KeywordHit@1：第 1 名页面是否包含答案关键词（粗粒度“找对页”的代理）；
- MRR：期望资料排名的倒数均值。

用途：作为 pytest 的一部分跑，或单独作为发布前门禁：
    python tests/eval/golden_gate.py --baseline tests/eval/baseline.json

这不是教学效果评测，也不替代真实模型/人工验收；它只防止“检索明显退化”。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

EVAL_DIR = Path(__file__).resolve().parent
DATASET = EVAL_DIR / "dataset"
GOLDEN_PATH = EVAL_DIR / "golden_set.json"
BASELINE_PATH = EVAL_DIR / "baseline.json"
DISTRACTOR = DATASET / "distractor_无关资料.txt"

DEFAULT_THRESHOLDS = {
    "recall_at_5": 0.95,
    "hit_at_1": 0.85,
    "keyword_hit_at_1": 0.85,
    "mrr": 0.85,
    "pass_rate": 0.9,
}

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


def load_documents() -> Dict[str, str]:
    docs: Dict[str, str] = {}
    materials = DATASET / "materials"
    if materials.is_dir():
        for path in sorted(materials.glob("*.txt")):
            docs[path.name] = path.read_text(encoding="utf-8", errors="ignore")
    if DISTRACTOR.is_file():
        docs[DISTRACTOR.name] = DISTRACTOR.read_text(encoding="utf-8", errors="ignore")
    return docs


def load_golden(path: Path = GOLDEN_PATH) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError("黄金集为空或格式不正确")
    return cases


def run_golden() -> Dict[str, Any]:
    from rank_bm25 import BM25Okapi

    docs = load_documents()
    if not docs:
        raise RuntimeError("找不到评测资料（tests/eval/dataset/materials）")
    names = list(docs.keys())
    corpus = [tokenize(docs[name]) for name in names]
    bm25 = BM25Okapi(corpus)

    cases = load_golden()
    rows: List[Dict[str, Any]] = []
    for case in cases:
        question = str(case.get("question") or "")
        expected = str(case.get("expected_source") or "")
        keyword = str(case.get("keyword") or "")
        scores = bm25.get_scores(tokenize(question))
        ranked = sorted(range(len(names)), key=lambda i: (-float(scores[i]), names[i]))
        ranked_names = [names[i] for i in ranked]
        try:
            rank = ranked_names.index(expected) + 1
        except ValueError:
            rank = None
        top1 = ranked_names[0] if ranked_names else ""
        top1_text = docs.get(top1, "").lower()
        rows.append({
            "id": case.get("id"),
            "question": question,
            "expected_source": expected,
            "rank": rank,
            "top1": top1,
            "recall_at_5": bool(rank and rank <= 5),
            "hit_at_1": bool(rank == 1),
            "keyword_hit_at_1": bool(keyword and keyword.lower() in top1_text),
        })

    total = len(rows) or 1
    metrics = {
        "sample_count": len(rows),
        "recall_at_5": round(sum(r["recall_at_5"] for r in rows) / total, 4),
        "hit_at_1": round(sum(r["hit_at_1"] for r in rows) / total, 4),
        "keyword_hit_at_1": round(sum(r["keyword_hit_at_1"] for r in rows) / total, 4),
        "mrr": round(sum((1.0 / r["rank"]) for r in rows if r["rank"]) / total, 4),
        "pass_rate": round(sum(1 for r in rows if r["hit_at_1"] and r["keyword_hit_at_1"])
                           / total, 4),
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
        base_metrics = baseline.get("metrics") or {}
        for name, base_value in base_metrics.items():
            if name not in metrics:
                continue
            if float(metrics[name]) < float(base_value) - max_drop:
                failures.append(
                    f"{name}={metrics[name]} 低于基线 {base_value} 超过 {max_drop}")
    return {"ok": not failures, "failures": failures, "metrics": metrics,
            "thresholds": limits}


def _load_baseline(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="检索黄金集门禁")
    parser.add_argument("--baseline", default=str(BASELINE_PATH),
                        help="基线 JSON（默认 tests/eval/baseline.json；传空字符串可禁用）")
    parser.add_argument("--json", default="", help="把结果写入指定 JSON 文件")
    parser.add_argument("--max-drop", type=float, default=0.05,
                        help="相对基线允许的最大下降")
    args = parser.parse_args(argv)

    result = run_golden()
    baseline = _load_baseline(Path(args.baseline)) if args.baseline else None
    gate = check_gate(result, baseline=baseline, max_drop=args.max_drop)
    payload = {"result": result, "gate": gate}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if gate["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
