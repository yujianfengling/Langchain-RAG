"""智慧学伴比赛版 · AI 助手 Agent（ChatGPT 式会话 + 工具循环）。

复用 master tutor_agent 的范式：function-calling 循环、轮次/工具次数限额、
工具结果截断、"数字只出自工具、引用真实 id、证据不足明说" 防幻觉规则。
工具读取全部已上传资料（官方 bundle + 教师导入的独立评测会话），并可生成教案/试卷/导出 Word·PDF。
会话与文件持久化在教师私有目录，仅本人可见。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.deps import require_teacher
from config.path_config import get_data_dir
from utils.logger import logger

router = APIRouter()

MAX_ROUNDS = 6
MAX_TOOL_CALLS = 12
TOOL_RESULT_CHARS = 6000

_LOCAL_ROOT = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "SmartStudyPartner"


def _db():
    path = Path(get_data_dir()) / "assistant.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, user TEXT, title TEXT, updated_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conv TEXT, role TEXT, text TEXT, cards TEXT, files TEXT, created_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, user TEXT, name TEXT, format TEXT, path TEXT)")
    return db


def _rows(dataset: str, name: str):
    from api.routes.competition import _ALLOWED, _bundle_dir
    if dataset and dataset != "official":
        if not re.fullmatch(r"eval-[0-9a-f]{8}", dataset):
            raise HTTPException(400, "非法数据集编号")
        base = _LOCAL_ROOT / "imports" / dataset / "bundle"
    else:
        base = _bundle_dir()
    if name not in _ALLOWED:
        raise HTTPException(400, "非法文件")
    p = base / name
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _datasets() -> List[dict]:
    out = [{"id": "official", "label": "官方赛题数据"}]
    root = _LOCAL_ROOT / "imports"
    if root.is_dir():
        for d in sorted(root.glob("eval-*")):
            if (d / "bundle" / "manifest.json").is_file():
                out.append({"id": d.name, "label": f"导入评测 {d.name}"})
    return out


def _class_summary(dataset: str, quiz_id: Optional[str], class_id: Optional[str]) -> dict:
    students = _rows(dataset, "students.json") or []
    answers = _rows(dataset, "answers.json") or []
    quizzes = _rows(dataset, "quizzes.json") or []
    classes = _rows(dataset, "classes.json") or []
    kps = _rows(dataset, "kps.json") or []
    quiz = quiz_id or (quizzes[0]["id"] if quizzes else None)
    cls = class_id or (classes[0]["id"] if classes else None)
    members = [s["id"] for s in students if s["classId"] == cls]
    rows = [a for a in answers if a["quizId"] == quiz and a["studentId"] in members]
    per = {}
    for sid in members:
        r = [a for a in rows if a["studentId"] == sid]
        full = sum(a["fullScore"] for a in r)
        per[sid] = {"score": sum(a["score"] for a in r), "full": full,
                    "rate": round(sum(a["score"] for a in r) / full * 100, 1) if full else None}
    scored = [v["rate"] for v in per.values() if v["rate"] is not None]
    weak = []
    for kp in kps:
        st = next((s for s in kp.get("stats", []) if s["classId"] == cls), None)
        if st and st.get("scoreRate") is not None:
            weak.append({"indicator": kp.get("indicatorId") or kp["id"], "name": kp["name"], "rate": round(st["scoreRate"] * 100, 1)})
    weak.sort(key=lambda x: x["rate"])
    return {"dataset": dataset, "quiz": quiz, "class": cls, "students": len(members),
            "mean_rate": round(sum(scored) / len(scored), 1) if scored else None,
            "weakest": weak[:4], "students_rate": per}


def _student_profile(dataset: str, student_id: str, quiz_id: Optional[str]) -> dict:
    answers = _rows(dataset, "answers.json") or []
    quizzes = _rows(dataset, "quizzes.json") or []
    diagnoses = (_rows(dataset, "diagnoses.json") or {}).get("diagnoses", [])
    history = []
    for q in quizzes:
        r = [a for a in answers if a["studentId"] == student_id and a["quizId"] == q["id"]]
        full = sum(a["fullScore"] for a in r)
        history.append({"quiz": q["id"], "name": q.get("name"), "score": sum(a["score"] for a in r),
                        "full": full, "rate": round(sum(a["score"] for a in r) / full * 100, 1) if full else None})
    ds = [d for d in diagnoses if d["student_id"] == student_id and (not quiz_id or d["node_id"] == quiz_id)]
    return {"student": student_id, "history": history,
            "indicators": [{"indicator": d["indicator_id"], "case": d.get("case"), "score": d.get("score"),
                            "full": d.get("full_score"), "coverage": d.get("coverage"),
                            "findings": [f.get("fact") for f in d.get("question_findings", [])][:3]} for d in ds]}


def _exam_items(dataset: str, quiz_id: Optional[str]) -> dict:
    questions = _rows(dataset, "questions.json") or []
    answers = _rows(dataset, "answers.json") or []
    quizzes = _rows(dataset, "quizzes.json") or []
    quiz = quiz_id or (quizzes[0]["id"] if quizzes else None)
    items = []
    for q in questions:
        r = [a for a in answers if a["quizId"] == quiz and a["questionId"] == q["id"]]
        full = sum(a["fullScore"] for a in r)
        items.append({"item": q["id"], "stem": q.get("stem", "")[:60], "indicator": q.get("kpId"),
                      "rate": round(sum(a["score"] for a in r) / full * 100, 1) if full else None, "n": len(r)})
    items.sort(key=lambda x: (x["rate"] is None, x["rate"] if x["rate"] is not None else 0))
    return {"quiz": quiz, "items": items[:20]}


def _curriculum(dataset: str, indicator_id: Optional[str]) -> dict:
    cm = _rows(dataset, "curriculum_map.json") or {}
    inds = cm.get("indicators", [])
    if indicator_id:
        inds = [i for i in inds if i["indicator_id"] == indicator_id]
    return {"indicators": [{"indicator": i["indicator_id"], "requirement": i.get("requirement"),
                            "standard": i.get("standard", {}).get("code"),
                            "textbook": [{"page": p.get("printed"), "anchor": p.get("anchor")} for p in i.get("textbook_pages", [])][:3]}
                           for i in inds[:6]]}


TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "read_class", "description": "读取某班某测验整体学情：均值/最弱考点/每个学生得分率", "parameters": {"type": "object", "properties": {"dataset": {"type": "string"}, "quiz_id": {"type": "string"}, "class_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_student", "description": "读取某学生跨次测验历史与考点诊断/错因/覆盖", "parameters": {"type": "object", "properties": {"dataset": {"type": "string"}, "student_id": {"type": "string"}, "quiz_id": {"type": "string"}}, "required": ["student_id"]}}},
    {"type": "function", "function": {"name": "read_exam", "description": "读取某测验逐题得分率（低分在前）", "parameters": {"type": "object", "properties": {"dataset": {"type": "string"}, "quiz_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_curriculum", "description": "读取课标要求与教材页出处", "parameters": {"type": "object", "properties": {"dataset": {"type": "string"}, "indicator_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "list_datasets", "description": "列出全部已上传/导入的数据集", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "generate_plan", "description": "生成讲评教案（JSON）", "parameters": {"type": "object", "properties": {"dataset": {"type": "string"}, "class_id": {"type": "string"}, "quiz_id": {"type": "string"}, "custom": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "generate_paper", "description": "生成查漏补缺强化卷（JSON）", "parameters": {"type": "object", "properties": {"dataset": {"type": "string"}, "class_id": {"type": "string"}, "quiz_id": {"type": "string"}, "style": {"type": "string"}, "count": {"type": "integer"}, "custom": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "export_file", "description": "把本会话最近生成的试卷/教案导出为 Word 或 PDF 供打印", "parameters": {"type": "object", "properties": {"format": {"type": "string", "enum": ["docx", "pdf"]}, "kind": {"type": "string", "enum": ["paper", "plan"]}}, "required": ["format"]}}},
]


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(min_length=1, max_length=2000)


def _paper_context(dataset: str, quiz: Optional[str], cls: Optional[str]) -> dict:
    summary = _class_summary(dataset, quiz, cls)
    questions = _rows(dataset, "questions.json") or []
    weak_ids = [w["indicator"] for w in summary["weakest"]]
    originals = [{"item_id": q["id"], "stem": q.get("stem", ""), "full_score": q.get("fullScore"),
                  "indicator": q.get("kpId"), "source": q.get("sourceLocation", "")}
                 for q in questions if any(w in (q.get("kpId") or "") for w in weak_ids)][:16]
    cm = _rows(dataset, "curriculum_map.json") or {}
    textbook = [{"page": p.get("printed"), "anchor": p.get("anchor")}
                for i in cm.get("indicators", []) if i["indicator_id"] in weak_ids
                for p in i.get("textbook_pages", []) if p.get("verified")][:8]
    return {"summary": summary, "originals": originals, "textbook": textbook}


def _export_artifact(state: dict, user: str, fmt: str, kind: str) -> dict:
    from api.routes.competition import _build_paper_docx
    artifact = state["artifacts"].get(kind)
    if not artifact:
        return {"error": f"本会话尚未生成{('试卷' if kind == 'paper' else '教案')}，请先调用 generate_{'paper' if kind == 'paper' else 'plan'}"}
    if kind == "paper":
        docx_bytes = _build_paper_docx(artifact, True, artifact.get("_class", ""), artifact.get("_quiz", ""))
    else:
        import io
        from docx import Document
        doc = Document()
        doc.add_heading(artifact.get("title", "讲评教案"), level=0)
        for sec in artifact.get("stages", []):
            doc.add_heading(sec.get("name", "环节"), level=1)
            doc.add_paragraph(sec.get("activity", ""))
        buf = io.BytesIO()
        doc.save(buf)
        docx_bytes = buf.getvalue()
    fid = uuid.uuid4().hex[:12]
    fdir = Path(get_data_dir()) / "assistant-files"
    fdir.mkdir(parents=True, exist_ok=True)
    path = fdir / f"{fid}.docx"
    path.write_bytes(docx_bytes)
    name = f"{'强化卷' if kind == 'paper' else '讲评教案'}.docx"
    if fmt == "pdf":
        import shutil
        soffice = os.getenv("PAPER_SOFFICE") or shutil.which("soffice") or "C:/Program Files/LibreOffice/program/soffice.exe"
        if not Path(soffice).is_file():
            return {"error": "本机未找到 LibreOffice，无法转 PDF；可导出 Word。"}
        import tempfile
        with tempfile.TemporaryDirectory(prefix=".agent-paper-") as tmp:
            src = Path(tmp) / "a.docx"
            src.write_bytes(docx_bytes)
            proc = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                                  capture_output=True, timeout=180)
            pdf = Path(tmp) / "a.pdf"
            if proc.returncode != 0 or not pdf.is_file():
                return {"error": "PDF 转换失败，可导出 Word。"}
            path = fdir / f"{fid}.pdf"
            path.write_bytes(pdf.read_bytes())
        name = f"{'强化卷' if kind == 'paper' else '讲评教案'}.pdf"
    with closing(_db()) as db:
        db.execute("INSERT INTO files VALUES(?,?,?,?,?)", (fid, user, name, fmt, str(path)))
        db.commit()
    ref = {"id": fid, "name": name, "format": fmt}
    state["files"].append(ref)
    return ref


def _dispatch(name: str, args: dict, state: dict, user: str) -> Any:
    import asyncio
    dataset = args.get("dataset") or "official"
    if name == "read_class":
        return _class_summary(dataset, args.get("quiz_id"), args.get("class_id"))
    if name == "read_student":
        return _student_profile(dataset, args["student_id"], args.get("quiz_id"))
    if name == "read_exam":
        return _exam_items(dataset, args.get("quiz_id"))
    if name == "read_curriculum":
        return _curriculum(dataset, args.get("indicator_id"))
    if name == "list_datasets":
        return _datasets()
    if name in ("generate_plan", "generate_paper"):
        from api.routes.competition import (AiPaperRequest, LessonPlanRequest, ai_paper, ai_lesson_plan)
        ctx = _paper_context(dataset, args.get("quiz_id"), args.get("class_id"))
        s = ctx["summary"]
        if name == "generate_paper":
            if not s["class"] or not s["quiz"]:
                return {"ok": False, "error": "当前数据没有可用的班级或测验，无法出卷"}
            body = AiPaperRequest(class_id=s["class"], quiz_id=s["quiz"],
                                  class_label=s["class"], quiz_label=s["quiz"],
                                  style=args.get("style") or "变式提升", count=int(args.get("count") or 10),
                                  custom=args.get("custom") or "", weak=s["weakest"],
                                  originals=ctx["originals"], textbook=ctx["textbook"])
            resp = asyncio.run(ai_paper(body, user))
            data = json.loads(resp.body)
            paper = data.get("paper") or {}
            paper["_class"] = s["class"]
            paper["_quiz"] = s["quiz"]
            state["artifacts"]["paper"] = paper
            state["cards"].append({"kind": "paper", "title": paper.get("title", "强化卷"),
                                   "lines": [f"{sec.get('name', '')}：{len(sec.get('questions', []))} 题" for sec in paper.get("sections", [])]})
            ref = _export_artifact(state, user, "docx", "paper")
            return {"ok": True, "title": paper.get("title"), "sections": len(paper.get("sections", [])), "file": ref}
        if not s["class"] or not s["quiz"]:
            return {"ok": False, "error": "当前数据没有可用的班级或测验，无法生成教案"}
        body = LessonPlanRequest(quiz_label=s["quiz"], class_label=s["class"],
                                   theme=(s["weakest"][0]["name"] if s["weakest"] else "讲评"),
                                   minutes=int(args.get("minutes") or 40), custom=args.get("custom") or "",
                                   standards=[], textbook=ctx["textbook"], exam_analysis=s["weakest"],
                                   profiles=[], segments=[])
        resp = asyncio.run(ai_lesson_plan(body, user))
        data = json.loads(resp.body)
        plan = data.get("plan") or {}
        state["artifacts"]["plan"] = plan
        state["cards"].append({"kind": "plan", "title": plan.get("title", "讲评教案"),
                               "lines": [f"{st.get('name', '')}（{st.get('minutes', '')} 分钟）" for st in plan.get("stages", [])]})
        ref = _export_artifact(state, user, "docx", "plan")
        return {"ok": True, "stages": len(plan.get("stages", [])), "file": ref}
    if name == "export_file":
        return _export_artifact(state, user, args.get("format") or "docx", args.get("kind") or "paper")
    return {"error": f"未知工具 {name}"}


@router.post("/competition/agent/chat")
async def agent_chat(body: ChatRequest, user: str = Depends(require_teacher)) -> JSONResponse:
    from llm import get_llm

    conv = body.conversation_id
    with closing(_db()) as db:
        if conv:
            row = db.execute("SELECT id,title FROM conversations WHERE id=? AND user=?", (conv, user)).fetchone()
            if not row:
                raise HTTPException(404, "会话不存在")
            history = db.execute("SELECT role,text FROM messages WHERE conv=? ORDER BY created_at", (conv,)).fetchall()
        else:
            conv = uuid.uuid4().hex[:12]
            title = body.message[:24]
            db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (conv, user, title, time.strftime("%Y-%m-%d %H:%M:%S")))
            history = []
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex[:12], conv, "user", body.message, "[]", "[]", time.strftime("%Y-%m-%d %H:%M:%S")))
        db.commit()

    datasets = _datasets()
    schema = json.loads(json.dumps(TOOLS_SCHEMA))
    for t in schema:
        props = t["function"]["parameters"].get("properties", {})
        if "dataset" in props:
            props["dataset"]["enum"] = [d["id"] for d in datasets]
    context = json.dumps({"datasets": datasets}, ensure_ascii=False)
    system = (
        "你是七年级数学教师的 AI 助手「智慧学伴」。你可以调用工具读取全部已上传资料（官方赛题数据与教师导入的独立评测数据），"
        "并生成讲评教案、查漏补缺强化卷、导出 Word/PDF。规则：所有数字与事实只来自工具返回；引用真实 item/indicator/record 编号；"
        "证据不足必须明说，不编造分数、页码或学生表现；掌握度由程序判定而非你猜测。"
        "生成试卷/教案后如教师需要打印，主动调用 export_file。回答用简洁中文。"
        "需要数据或要生成/导出时，必须立即调用对应工具，不要只输出计划或描述。"
        f"当前可用数据集：{context}"
    )
    messages: List[dict] = [{"role": "system", "content": system}]
    for role, text in history[-12:]:
        messages.append({"role": "user" if role == "user" else "assistant", "content": text})
    messages.append({"role": "user", "content": body.message})

    cards: List[dict] = []
    files: List[dict] = []
    state: Dict[str, Any] = {"artifacts": {}, "cards": cards, "files": files}
    provider = get_llm(temperature=0.2, max_tokens=1500).provider
    tool_calls = 0
    executed = False
    final_text = ""
    for _round in range(MAX_ROUNDS):
        resp = await run_in_threadpool(provider.chat_with_tools, messages, schema,
                                       temperature=0.2, max_tokens=1500)
        calls = resp.get("tool_calls") or []
        if not calls:
            if not executed and _round < 2:
                messages.append({"role": "assistant", "content": resp.get("answer") or ""})
                messages.append({"role": "user", "content": "请立即调用工具获取真实数据并执行，不要仅描述计划。"})
                continue
            final_text = resp.get("answer") or ""
            break
        executed = True
        messages.append(resp.get("raw_message") or {"role": "assistant", "content": resp.get("answer") or ""})
        for call in calls:
            if tool_calls >= MAX_TOOL_CALLS:
                break
            tool_calls += 1
            fn = call.get("function") or {}
            name = fn.get("name") or call.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            try:
                result = await run_in_threadpool(_dispatch, name, args, state, user)
            except Exception as exc:
                result = {"error": str(exc)}
            payload = json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_CHARS]
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": payload})
    if not final_text:
        final_text = "本轮工具调用已达上限，先给出已有结果；可继续追问。"

    with closing(_db()) as db:
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex[:12], conv, "assistant", final_text,
                    json.dumps(cards, ensure_ascii=False), json.dumps(files, ensure_ascii=False),
                    time.strftime("%Y-%m-%d %H:%M:%S")))
        db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M:%S"), conv))
        db.commit()
    return JSONResponse({"conversation_id": conv,
                         "message": {"id": uuid.uuid4().hex[:12], "role": "assistant", "text": final_text,
                                     "cards": cards, "files": files}})


@router.get("/competition/agent/conversations")
def list_conversations(user: str = Depends(require_teacher)) -> JSONResponse:
    with closing(_db()) as db:
        rows = db.execute("SELECT id,title,updated_at FROM conversations WHERE user=? ORDER BY updated_at DESC", (user,)).fetchall()
    return JSONResponse({"conversations": [{"id": r[0], "title": r[1], "updated_at": r[2]} for r in rows]})


@router.get("/competition/agent/conversations/{conv}")
def get_conversation(conv: str, user: str = Depends(require_teacher)) -> JSONResponse:
    with closing(_db()) as db:
        row = db.execute("SELECT id,title FROM conversations WHERE id=? AND user=?", (conv, user)).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在")
        msgs = db.execute("SELECT id,role,text,cards,files,created_at FROM messages WHERE conv=? ORDER BY created_at", (conv,)).fetchall()
    return JSONResponse({"id": row[0], "title": row[1], "messages": [
        {"id": m[0], "role": m[1], "text": m[2], "cards": json.loads(m[3] or "[]"),
         "files": json.loads(m[4] or "[]"), "created_at": m[5]} for m in msgs]})


@router.delete("/competition/agent/conversations/{conv}")
def delete_conversation(conv: str, user: str = Depends(require_teacher)) -> JSONResponse:
    with closing(_db()) as db:
        db.execute("DELETE FROM messages WHERE conv=? AND conv IN (SELECT id FROM conversations WHERE user=?)", (conv, user))
        db.execute("DELETE FROM conversations WHERE id=? AND user=?", (conv, user))
        db.commit()
    return JSONResponse({"ok": True})


@router.get("/competition/agent/file/{fid}")
def get_file(fid: str, user: str = Depends(require_teacher)) -> Response:
    with closing(_db()) as db:
        row = db.execute("SELECT name,format,path FROM files WHERE id=? AND user=?", (fid, user)).fetchone()
    if not row or not Path(row[2]).is_file():
        raise HTTPException(404, "文件不存在")
    from urllib.parse import quote
    media = "application/pdf" if row[1] == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(content=Path(row[2]).read_bytes(), media_type=media,
                    headers={"Content-Disposition": f"attachment; filename=\"paper.{row[1]}\"; filename*=UTF-8''{quote(row[0])}"})