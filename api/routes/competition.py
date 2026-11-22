"""
智慧学伴比赛版 · 受控数据接口
============================================================
官方赛题原包、官方答卷、含答卷的生成结果只保存在本地私有目录，
不进入 frontend/public，也不会被打包进公开静态资源。

页面测试时：
  - 默认（无参数）：前端演示样例，不调用本接口。
  - ?source=official：前端调用 GET /api/competition/official-bundle，
    需要教师登录，服务端从私有目录读取 bundle 后返回。

私有目录由环境变量 COMPETITION_OFFICIAL_BUNDLE_DIR 指定；未配置时返回 404，
绝不回退到公开静态目录。只读、只放行固定白名单 JSON 文件。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from api.deps import require_teacher
from utils.logger import logger

router = APIRouter()

# 只允许这些文件被读出，防止路径穿越或误暴露私有目录里的其他文件
_ALLOWED = (
    "manifest.json", "classes.json", "topics.json", "quizzes.json",
    "students.json", "kps.json", "questions.json", "answers.json",
    "curriculum_map.json", "diagnoses.json", "case_book.json",
    "events.json", "replay.json", "anomalies.json", "review_plans.json",
)

_DEFAULT_PRIVATE_DIR = Path("D:/Document/langchain-rag-work/competition-data/official-bundle")
_DEFAULT_RESOURCE_DIR = Path(
    "D:/Document/langchain-rag-work/official-competition-data/赛题数据/赛题资料包/5_课程标准及相关教学资源"
)
# 只允许赛方切片与课标文件，杜绝路径穿越
_ALLOWED_PDF = {
    "B01": "B01_相关资料切片.pdf", "B02": "B02_相关资料切片.pdf", "B03": "B03_相关资料切片.pdf",
    "B04": "B04_相关资料切片.pdf", "B05": "B05_相关资料切片.pdf", "B06": "B06_相关资料切片.pdf",
    "B07": "B07_相关资料切片.pdf", "B08": "B08_相关资料切片.pdf",
    "CR": "课程标准_2022.pdf",
}


def _bundle_dir() -> Path:
    raw = (os.getenv("COMPETITION_OFFICIAL_BUNDLE_DIR") or "").strip()
    return Path(raw) if raw else _DEFAULT_PRIVATE_DIR


@router.get("/competition/official-bundle")
async def official_bundle(_: str = Depends(require_teacher)) -> JSONResponse:
    """返回本地私有目录里的官方 bundle（教师登录 + 环境变量双重受控）。"""
    directory = _bundle_dir()
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=(
            "官方数据未配置：请设置 COMPETITION_OFFICIAL_BUNDLE_DIR 指向本地私有 bundle 目录。"
            "当前不会回退到公开静态资源。"
        ))

    core = _ALLOWED[:8]
    optional = _ALLOWED[8:]
    payload: Dict[str, object] = {}
    for name in core + tuple(n for n in optional if (directory / n).is_file()):
        path = directory / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"官方 bundle 缺少文件：{name}")
        try:
            payload[name.removesuffix(".json")] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error(f"[competition] 读取官方 bundle 失败：{name}: {exc}")
            raise HTTPException(status_code=500, detail=f"读取官方数据失败：{name}") from exc

    manifest = payload.get("manifest") or {}
    if manifest.get("mode") != "real":
        raise HTTPException(status_code=409, detail="官方 bundle 的 manifest.mode 不是 real，拒绝返回")

    logger.info(f"[competition] 受控接口返回官方 bundle：source={manifest.get('source')} dir={directory}")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


@router.get("/competition/official-pdf/{material_id}")
async def official_pdf(material_id: str, _: str = Depends(require_teacher)) -> Response:
    """受控读取赛方教材切片 / 课标 PDF；material_id 只能是白名单编号。"""
    name = _ALLOWED_PDF.get(material_id)
    if not name:
        raise HTTPException(status_code=404, detail=f"未授权的教材编号：{material_id}")
    root = _DEFAULT_RESOURCE_DIR
    path = root / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"教材文件不存在：{name}")
    logger.info(f"[competition] 受控接口返回教材 PDF：{material_id}")
    return Response(
        content=path.read_bytes(), media_type="application/pdf",
        headers={"Cache-Control": "no-store", "Content-Disposition": f'inline; filename="{material_id}.pdf"'},
    )


_DEFAULT_SRC_DIR = Path("D:/Document/langchain-rag-work/competition-data/official")


@router.get("/competition/export-review")
def export_review(_: str = Depends(require_teacher)) -> JSONResponse:
    directory = _bundle_dir().parent / "deliverable"
    filenames = (
        "curriculum_map.json", "class_analysis.json", "student_analysis.json",
        "study_recommendations.json", "evidence_index.json", "data_summary.json",
        "field_dictionary.json", "data_quality.csv", "recommendations.csv", "crosscheck.json",
        "same_question_comparison.json", "review_plans.json",
    )
    try:
        exported = json.loads((directory / "review_plans.json").read_text(encoding="utf-8"))
        current = json.loads((_bundle_dir() / "review_plans.json").read_text(encoding="utf-8"))
        plan = exported["strategies"]["common"]["budgets"]["3"]
        current_plan = current["strategies"]["common"]["budgets"]["3"]
        files = [{"name": name, "bytes": (directory / name).stat().st_size} for name in filenames]
        workload = plan["workload"]
    except (OSError, ValueError, KeyError):
        raise HTTPException(status_code=409, detail="成果包不完整或版本不匹配，请先导出并校验") from None
    matches = all(plan.get(key) == current_plan.get(key) for key in (
        "lecture_items", "pretest_tasks", "coverage_audit", "workload",
    ))
    return JSONResponse(content={
        "files": files,
        "review": {
            "node_id": exported["node_id"], "class_id": exported["class_id"], "theme": exported["theme"],
            "budget": 3, "lecture_items": [x["item_id"] for x in plan["lecture_items"]],
            "lecture_assignments": workload["lecture_assignments"],
            "pretest_items": [x["item_id"] for x in plan["pretest_tasks"]],
            "pretest_assignments": workload["pretest_assignments"],
        },
        "consistency": {
            "matches_current": matches,
            "note": ("磁盘成果包与当前页面的系统原方案一致（共性策略/讲评预算3）。" if matches
                     else "磁盘成果包与当前页面的系统原方案不一致，请重新导出核验。")
                    + "这里只核对选题、分配和覆盖计数；完整校验请运行 export_package.py verify。",
        },
    }, headers={"Cache-Control": "no-store"})


@router.post("/competition/review-plan/recompute")
async def review_plan_recompute(body: dict, _: str = Depends(require_teacher)) -> JSONResponse:
    """用会话现场证据重算讲评方案（仅方案层；不写回官方答卷/评分/统计）。"""
    import sys
    root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(root / "competition_pipeline"))
    import review_plan  # noqa: E402

    src = Path(os.getenv("COMPETITION_OFFICIAL_SRC_DIR") or _DEFAULT_SRC_DIR)
    analysis = src / "analysis"
    if not (src / "scope_index.json").is_file() or not (analysis / "diagnoses.json").is_file():
        raise HTTPException(status_code=404, detail="讲评方案所需的管线输出未配置")
    node = body.get("node_id") or "N01"
    class_id = body.get("class_id") or "DC01"
    theme = body.get("theme") or "T01"
    budget = int(body.get("budget") or 3)
    strategy = body.get("strategy") or "common"
    if strategy not in ("common", "pretest", "naive"):
        raise HTTPException(status_code=400, detail="strategy 只能是 common/pretest/naive")
    if budget not in (2, 3):
        raise HTTPException(status_code=400, detail="首版预算只支持 2 或 3")
    students = json.loads(((_bundle_dir()) / "students.json").read_text(encoding="utf-8"))
    members = [s["id"] for s in students if s.get("classId") == class_id]
    scope = json.loads((src / "scope_index.json").read_text(encoding="utf-8"))
    cmap = json.loads((analysis / "curriculum_map.json").read_text(encoding="utf-8"))["indicators"]
    inds = {c["indicator_id"] for c in cmap if c["theme_id"] == theme}
    extra = []
    for x in body.get("extra_evidence") or []:
        sid, iid, kind = x.get("student_id"), x.get("item_id"), x.get("kind")
        if sid not in members or kind not in ("correct", "mid_wrong", "combine_wrong", "transcription_wrong", "final_wrong", "manual"):
            raise HTTPException(status_code=400, detail=f"现场证据非法：{x}")
        if scope["item_indicator"].get(iid) not in inds:
            raise HTTPException(status_code=400, detail=f"现场证据题目超出主题范围：{iid}")
        if kind == "manual":
            continue  # 待人工核对不进入自动重算
        extra.append({"student_id": sid, "item_id": iid, "kind": kind,
                      "record_id": x.get("record_id", "现场作答")})
    base = review_plan.generate(src, analysis, members, node, class_id, theme, budgets=(2, 3))
    new = review_plan.generate(src, analysis, members, node, class_id, theme, budgets=(2, 3), extra=extra)
    b = base["strategies"][strategy]["budgets"][str(budget)] if strategy != "naive" else base["comparison"][str(budget)]["naive_plan"]
    n = new["strategies"][strategy]["budgets"][str(budget)] if strategy != "naive" else new["comparison"][str(budget)]["naive_plan"]
    changes = []
    bi = {x["item_id"] for x in b.get("lecture_items", [])}
    ni = {x["item_id"] for x in n.get("lecture_items", [])}
    for q in sorted(bi - ni):
        changes.append(f"讲评题移除 {q}（新证据改变排序或覆盖）")
    for q in sorted(ni - bi):
        changes.append(f"讲评题新增 {q}")
    bp = {t["item_id"] for t in b.get("pretest_tasks", [])}
    np_ = {t["item_id"] for t in n.get("pretest_tasks", [])}
    for q in sorted(bp - np_):
        changes.append(f"补测任务移除 {q}（相关学生证据已足够或已覆盖）")
    for q in sorted(np_ - bp):
        changes.append(f"补测任务新增 {q}")
    bs, ns = set(b.get("covered_students", [])), set(n.get("covered_students", []))
    for s in sorted(bs - ns):
        changes.append(f"学生 {s} 不再被本方案覆盖")
    for s in sorted(ns - bs):
        changes.append(f"学生 {s} 新被本方案覆盖")
    if b.get("covered_lost_points") != n.get("covered_lost_points"):
        changes.append(f"已定位失分点 {b.get('covered_lost_points')} → {n.get('covered_lost_points')}")
    bu, nu = b.get("uncovered", {}), n.get("uncovered", {})
    if len(bu.get("lost_points", [])) != len(nu.get("lost_points", [])):
        changes.append(f"未覆盖失分点 {len(bu.get('lost_points', []))} → {len(nu.get('lost_points', []))}（现场观察新增/消解失分）")
    bi = {(x['student'], x['indicator']): x['effective'] for x in bu.get('insufficient_students', [])}
    ni = {(x['student'], x['indicator']): x['effective'] for x in nu.get('insufficient_students', [])}
    for k in sorted(set(bi) | set(ni)):
        if bi.get(k) != ni.get(k):
            changes.append(f"学生 {k[0]} 在 {k[1]} 的有效证据 {bi.get(k, '已足够')} → {ni.get(k, '已足够')}")
    if not changes:
        changes.append("新证据未改变本策略/预算下的讲评题与补测任务（保留原安排）")
    logger.info(f"[competition] 讲评方案重算：{node}/{class_id}/{theme} budget={budget} strategy={strategy} extra={len(extra)}")
    audit = review_plan.compare_plans(b, n)
    return JSONResponse(content={"plans": base, "new_plans": new,
                                 "base": b, "new": n, "changes": changes, "audit": audit,
                                 "session_extra_evidence": extra},
                        headers={"Cache-Control": "no-store"})
