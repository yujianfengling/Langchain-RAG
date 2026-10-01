"""
教学效果评估脚本（2026-09-27）

一次跑完四类评测，产出可复现的报告：
  1) RAG      —— 检索是否命中、引用能否定位原文、材料没有答案时是否说明不知道、
                 加入无关资料后是否仍能正确回答
  2) 教学闭环 —— 判分 / 错题 / 掌握度 / 复习任务 / 教师学情是否一致，重复提交是否重复计数
  3) Agent    —— 是否依据该学生真实错题与进度给建议；改计划是否走确认机制
  4) 语音     —— 录音识别 → 问答 → 语音合成的完整流程与耗时，并导出音频供人工试听

用法（在仓库根目录）：
    # 1. 先启动隔离后端（必须 EDU_DATA_DIR 指向独立目录）
    $env:EDU_DATA_DIR="E:/WORKBUDDY/Game/git_text_eval_data"
    python -m uvicorn api.app:app --host 127.0.0.1 --port 8001
    # 2. 跑评测（会新建评测账号与课程，全部在隔离目录内）
    python tests/eval/run_eval.py --base http://127.0.0.1:8001 --suites rag,loop,agent,voice

产出：
    tests/eval/output/eval_report.json     机器可读的完整结果
    tests/eval/output/EVAL_REPORT.md       人读报告（样本数、通过率、失败案例、限制）
    tests/eval/output/listen/*.wav         语音样本（供人工试听，**不代替人工验收**）

原则：
- 真实模型调用与 Mock 不混记：本脚本默认调用**真实模型**；加 --mock 才用假数据（仅调试用）。
- 不把模拟数据写成真实学习成效：报告里所有结论都标注为"隔离评测账号"的结果。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

EVAL_DIR = Path(__file__).resolve().parent
DATASET = EVAL_DIR / "dataset"
OUTPUT = EVAL_DIR / "output"
LISTEN_DIR = OUTPUT / "listen"

PASSWORD = "eval-pass-123"
# 每次运行的唯一标识（用于生成干净的评测账号与幂等令牌）
_RUN_ID = "v1"


# ==================== HTTP 小工具 ====================

class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.tokens: Dict[str, str] = {}

    def _req(self, method: str, path: str, token: Optional[str] = None,
             body: Optional[dict] = None, raw: Optional[bytes] = None,
             content_type: Optional[str] = None, timeout: int = 600) -> Tuple[int, Any]:
        url = self.base + path
        data = raw if raw is not None else (
            json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        if token:
            req.add_header("X-User-Token", token)
        if content_type:
            req.add_header("Content-Type", content_type)
        elif body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            payload = resp.read()
            try:
                return resp.status, json.loads(payload.decode("utf-8"))
            except Exception:
                return resp.status, payload
        except urllib.error.HTTPError as e:
            raw_body = e.read()
            try:
                return e.code, json.loads(raw_body.decode("utf-8"))
            except Exception:
                return e.code, raw_body.decode("utf-8", "ignore")

    def get(self, path, token=None, **kw):
        return self._req("GET", path, token, **kw)

    def post(self, path, body=None, token=None, **kw):
        return self._req("POST", path, token, body=body, **kw)

    def put(self, path, body=None, token=None, **kw):
        return self._req("PUT", path, token, body=body, **kw)

    def upload(self, path: str, filename: str, content: bytes, token: str):
        boundary = "----evalboundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode()
        return self._req("POST", path, token, raw=body,
                         content_type=f"multipart/form-data; boundary={boundary}")
    def upload_form(self, path: str, fields: Dict[str, str]) -> Tuple[int, Any]:
        boundary = "----evalform"
        parts = b""
        for k, v in fields.items():
            parts += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                      f"{v}\r\n").encode("utf-8")
        parts += f"--{boundary}--\r\n".encode()
        return self._req("POST", path, raw=parts,
                         content_type=f"multipart/form-data; boundary={boundary}")


def ensure_user(c: Client, username: str) -> str:
    """注册或登录（重复运行时复用同一账号，保证评测集不漂移）"""
    status, body = c.post("/api/auth/register",
                          {"username": username, "password": PASSWORD})
    if status == 200 and isinstance(body, dict) and body.get("data", {}).get("token"):
        return body["data"]["token"]
    status, body = c.post("/api/auth/login", {"username": username, "password": PASSWORD})
    if status == 200 and isinstance(body, dict) and body.get("data", {}).get("token"):
        return body["data"]["token"]
    raise RuntimeError(f"无法创建/登录评测账号 {username}: {status} {body}")



def _kw_groups(spec_value) -> List[List[str]]:
    """
    关键字组规范化：
    - [["a","b"], ["c"]] → 每组至少命中一个
    - ["a","b"]         → 整组任选一个（扁平写法）
    - "a" / None        → 单组
    """
    if not spec_value:
        return []
    if isinstance(spec_value, str):
        return [[spec_value]]
    if all(isinstance(x, str) for x in spec_value):
        return [list(spec_value)]
    return [list(g) if isinstance(g, (list, tuple)) else [str(g)] for g in spec_value]


def _match_groups(text: str, spec_value) -> Tuple[bool, List[str]]:
    """返回 (是否全部组都命中, 未命中的组)"""
    groups = _kw_groups(spec_value)
    missing = []
    for g in groups:
        if not any(kw in text for kw in g):
            missing.append("|".join(g))
    return (not missing), missing


_MATERIAL_WORDS = ("材料", "资料", "知识库", "讲义", "本课程", "课本", "教材")
_NEGATION_WORDS = ("没有", "未", "无", "不含", "不涉及", "找不到", "不包含", "缺乏", "并未")
_GENERAL_MARKERS = ("一般性", "一般地", "通用", "常识", "补充", "通常", "延伸",
                    "额外说明", "教材之外", "课程之外")


def _sentences(text: str) -> List[str]:
    import re
    return [s for s in re.split(r"[。！？\n]+", text or "") if s.strip()]


def _fabricated_as_material(answer: str, tokens: List[str]) -> List[str]:
    """
    找出"把材料里没有的内容说成课程依据"的句子

    只有同时满足下面两点才算编造（避免把"材料中没有提到 X"这种正确回答误判为编造）：
    1) 句子含该关键词；
    2) 句子以材料为依据（含"材料/资料/知识库/讲义/本课程"等）**且**没有否定词。
    """
    bad = []
    for sent in _sentences(answer):
        if not any(tok in sent for tok in tokens):
            continue
        if any(neg in sent for neg in _NEGATION_WORDS):
            continue
        if any(w in sent for w in _MATERIAL_WORDS):
            bad.append(sent.strip()[:80])
    return bad


def _labels_general_knowledge(answer: str) -> bool:
    """补充的课外常识是否被明确标注（"一般性""常识""通常"等）"""
    return any(m in (answer or "") for m in _GENERAL_MARKERS)


# ==================== 结果收集 ====================

class Report:
    def __init__(self):
        self.suites: Dict[str, Dict[str, Any]] = {}
        self.started = datetime.now().isoformat(timespec="seconds")

    def suite(self, name: str) -> Dict[str, Any]:
        return self.suites.setdefault(name, {
            "name": name, "cases": [], "notes": [], "limits": [],
        })

    def case(self, suite: str, case_id: str, ok: bool, detail: str = "",
             expected: str = "", actual: str = "", extra: Optional[dict] = None):
        item = {"id": case_id, "ok": bool(ok), "detail": detail,
                "expected": expected, "actual": actual}
        if extra:
            item.update(extra)
        self.suite(suite)["cases"].append(item)
        flag = "PASS" if ok else "FAIL"
        print(f"  {flag} | [{suite}] {case_id} | {detail}")

    def summary(self) -> Dict[str, Any]:
        out = {}
        for name, s in self.suites.items():
            total = len(s["cases"])
            passed = len([x for x in s["cases"] if x["ok"]])
            out[name] = {
                "sample_count": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": round(passed / total, 3) if total else None,
                "failures": [x for x in s["cases"] if not x["ok"]],
                "notes": s["notes"],
                "limits": s["limits"],
            }
        return out


# ==================== 公共准备 ====================

def bootstrap(c: Client, suffix: str) -> Dict[str, Any]:
    """建评测课程 + 班级 + 固定材料 + 知识库（幂等：同名课程已存在则复用）"""
    teacher = ensure_user(c, "eval_teacher")
    status, body = c.get("/api/teacher/courses", token=teacher)
    courses = (body or {}).get("data", {}).get("courses", []) if status == 200 else []
    title = "评测课程：二叉树"
    course = next((x for x in courses if x["title"] == title), None)
    if not course:
        status, body = c.post("/api/teacher/courses", {"title": title,
                                                       "description": "教学效果评测用课程"},
                              token=teacher)
        if status != 200:
            if (body or {}).get("error_type") == "teacher_required":
                raise RuntimeError(
                    "评测账号还不是教师，无法建课。\n"
                    "评测需要一个受控开通的教师账号（创建课程不再自动授予教师身份）。\n"
                    "请在启动评测后端时指定初始教师：\n"
                    "  TEACHER_USERS=eval_teacher\n"
                    "  EDU_DATA_DIR=<隔离目录>\n"
                    "  python -m uvicorn api.app:app --host 127.0.0.1 --port 8001\n"
                    "（或用管理员账号生成教师邀请码后在此账号开通）")
            raise RuntimeError(f"创建评测课程失败：{status} {body}")
        course = body["data"]

    status, body = c.get(f"/api/teacher/courses/{course['id']}/classes", token=teacher)
    classes = (body or {}).get("data", {}).get("classes", []) if status == 200 else []
    klass = next((x for x in classes if x["name"] == "评测班"), None)
    if not klass:
        status, body = c.post(f"/api/teacher/courses/{course['id']}/classes",
                              {"name": "评测班"}, token=teacher)
        klass = body["data"]

    # 上传固定材料（同名覆盖）
    for path in sorted((DATASET / "materials").glob("*.txt")):
        c.upload(f"/api/teacher/courses/{course['id']}/materials?visibility=students",
                 path.name, path.read_bytes(), teacher)
    status, body = c.post(f"/api/teacher/courses/{course['id']}/knowledge/rebuild",
                          token=teacher)
    kb_ok = status == 200 and (body.get("data") or {}).get("ok")

    # 学生
    tokens = {}
    for key in ("stu_good", "stu_mid", "stu_other"):
        # 学生账号带 run_id：每轮都是干净状态，期望值（错题数/复习数）与历史无关
        username = f"eval_{key}_{suffix}" if suffix else f"eval_{key}"
        tokens[key] = ensure_user(c, username)
    for token in tokens.values():
        c.post("/api/student/courses/join", {"code": klass["join_code"]}, token=token)

    return {"teacher": teacher, "course": course, "class": klass,
            "students": tokens, "kb_ok": kb_ok}


# ==================== 1) RAG ====================

def run_rag(c: Client, ctx: Dict[str, Any], ds: dict, rep: Report,
            extra_material: bool = False, tag: str = ""):
    suite = "rag" + tag
    subject_id = ctx["course"]["subject_id"]
    samples = ds["rag"]["samples"]
    for s in samples:
        t0 = time.time()
        status, body = c.post("/api/chat", {"question": s["question"],
                                            "subject_id": subject_id},
                              token=ctx["students"]["stu_good"])
        elapsed = round(time.time() - t0, 2)
        if status != 200 or not isinstance(body, dict):
            rep.case(suite, s["id"], False, f"请求失败 {status}（{elapsed}s）",
                     expected="200 且有回答", actual=str(body)[:200])
            continue
        answer = str(body.get("answer") or body.get("response") or "")
        sources = body.get("sources") or []
        src_names = [str(x.get("file_name") or x.get("name") or "") for x in sources]

        if s.get("answerable"):
            # 1) 必须命中要点
            hit, missing = _match_groups(answer, s["must_contain_any"])
            # 2) 引用必须能定位到期望的资料
            cite_ok = (not s.get("expect_source")) or any(
                s["expect_source"] in name for name in src_names)
            ok = hit and cite_ok
            detail = (f"{elapsed}s 命中要点={hit} 引用定位={cite_ok} "
                      f"来源={src_names[:2]}" + (f" 未命中={missing}" if missing else ""))
            rep.case(suite, s["id"], ok, detail,
                     expected=f"包含要点且引用 {s.get('expect_source')}",
                     actual=answer[:160],
                     extra={"elapsed_s": elapsed, "sources": src_names,
                            "keyword_hit": hit, "citation_ok": cite_ok})
        else:
            # 材料里没有：必须明确说明不知道，且不能编造材料里没有的具体数字/术语
            # （本组样本的 must_contain_any 是扁平列表：命中任意一个即视为"承认不知道"）
            admits, missing = _match_groups(answer, s["must_contain_any"])
            # 判定"编造"的准确含义：把材料里没有的内容**说成课程依据**。
            # 允许的做法：明确说明材料未覆盖，并把补充的常识**标注为一般性知识**。
            fabricated = _fabricated_as_material(answer, s.get("must_not_assert", []))
            labelled_general = _labels_general_knowledge(answer)
            ok = admits and not fabricated
            rep.case(suite, s["id"], ok,
                     f"{elapsed}s 承认不知道={admits} 谎称材料有={fabricated or '无'} "
                     f"补充常识并标注={labelled_general}",
                     expected="明确说明材料中没有相关信息；如需补充常识必须标注为一般性知识",
                     actual=answer[:200],
                     extra={"elapsed_s": elapsed, "sources": src_names,
                            "admits_unknown": admits, "fabricated_as_material": fabricated,
                            "general_knowledge_labelled": labelled_general})

    if extra_material:
        rep.case(suite, "rag-extra-material", ctx["kb_ok"],
                 "已上传无关资料并重建知识库后重跑同一批样本",
                 expected="检索/回答仍正确", actual=f"知识库可用={ctx['kb_ok']}")


def run_rag_with_distractor(c: Client, ctx: Dict[str, Any], ds: dict, rep: Report):
    """加入无关资料后是否仍能正确回答（对照实验）"""
    distractor = EVAL_DIR / "dataset" / "distractor_无关资料.txt"
    c.upload(f"/api/teacher/courses/{ctx['course']['id']}/materials?visibility=students",
             distractor.name, distractor.read_bytes(), ctx["teacher"])
    status, body = c.post(f"/api/teacher/courses/{ctx['course']['id']}/knowledge/rebuild",
                          token=ctx["teacher"])
    ctx["kb_ok"] = status == 200 and (body.get("data") or {}).get("ok")
    run_rag(c, ctx, ds, rep, extra_material=True, tag="_with_distractor")


# ==================== 2) 教学闭环 ====================

def run_loop(c: Client, ctx: Dict[str, Any], ds: dict, rep: Report):
    suite = "loop"
    from services import quiz_service  # 仅用于读取原始记录做核对（隔离库）
    course = ctx["course"]
    klass = ctx["class"]
    subject_id = course["subject_id"]
    spec = ds["loop"]

    # 用评测集里的固定题目入库（走既有题库）
    questions = [{
        "type": q["type"],
        "question": f"{q['question']}【{q['id']}】",
        "options": q["options"],
        "answer": q["options"][q["answer_index"]],
        "explanation": f"标准答案：{q['options'][q['answer_index']]}",
    } for q in spec["questions"]]
    public = quiz_service.persist_questions(subject_id=subject_id,
                                           knowledge_point_name="二叉树（评测）",
                                           questions=questions, source="eval")
    by_index = {i: q for i, q in enumerate(public)}
    option_of = {}
    for i, q in enumerate(public):
        spec_q = spec["questions"][i]
        correct_option = q["options"][spec_q["answer_index"]]["id"]
        wrong_option = next(o["id"] for o in q["options"]
                            if o["id"] != correct_option)
        option_of[spec_q["id"]] = {"question": q, "correct": correct_option,
                                   "wrong": wrong_option}

    assignment = None
    status, body = c.get(f"/api/teacher/courses/{course['id']}/assignments",
                         token=ctx["teacher"])
    existing = [a for a in (body.get("data", {}).get("assignments") or [])
                if a["title"] == "评测作业：二叉树遍历"]
    if existing:
        assignment = existing[0]
    else:
        status, body = c.post(f"/api/teacher/courses/{course['id']}/assignments",
                              {"class_id": klass["id"], "title": "评测作业：二叉树遍历",
                               "description": "评测集题目", "due_date": "2026-12-31",
                               "knowledge_points": ["二叉树遍历"],
                               "question_ids": [q["id"] for q in public]},
                              token=ctx["teacher"])
        assignment = body["data"]
        c.post(f"/api/teacher/assignments/{assignment['id']}/publish",
               token=ctx["teacher"])

    # 按 plan 作答
    results: Dict[str, Dict[str, int]] = {}
    for stu in spec["students"]:
        token = ctx["students"][stu["key"]]
        wrong = correct = 0
        for qid, want in stu["plan"].items():
            info = option_of[qid]
            pick = info["correct"] if want == "correct" else info["wrong"]
            status, body = c.post("/api/quiz/answer",
                                  {"question_id": info["question"]["id"],
                                   "user_answer": pick,
                                   "client_token": f"eval-{_RUN_ID}-{stu['key']}-{qid}"},
                                  token=token)
            is_correct = bool((body.get("data") or {}).get("is_correct")) if status == 200 else None
            if want == "correct":
                correct += 1 if is_correct else 0
            else:
                wrong += 1 if is_correct is False else 0
        results[stu["key"]] = {"correct": correct, "wrong": wrong}
        exp = stu["expect"]
        rep.case(suite, f"judge-{stu['key']}", correct + wrong == len(stu["plan"]),
                 f"判分与预期一致：对 {correct} 错 {wrong}",
                 expected=f"对 {len([v for v in stu['plan'].values() if v=='correct'])} "
                          f"错 {len([v for v in stu['plan'].values() if v=='wrong'])}",
                 actual=f"对 {correct} 错 {wrong}")

        # 错题本
        status, body = c.get(f"/api/quiz/wrong?subject_id={subject_id}", token=token)
        wrong_n = len((body.get("data") or []))
        rep.case(suite, f"wrongbook-{stu['key']}", wrong_n == exp["wrong_count"],
                 f"错题本 {wrong_n} 道",
                 expected=f"{exp['wrong_count']} 道", actual=f"{wrong_n} 道",
                 extra={"wrong_count": wrong_n})

        # 掌握度：有证据才显示，无证据不虚构
        status, body = c.get(f"/api/student/mastery?subject_id={subject_id}", token=token)
        points = (body.get("data") or {}).get("points") or []
        has_evidence = any((p.get("evidence_count") or p.get("correct_count")
                            or p.get("wrong_count")) for p in points)
        rep.case(suite, f"mastery-{stu['key']}", bool(points) and has_evidence,
                 f"掌握度知识点 {len(points)} 个，含证据={has_evidence}",
                 expected="有知识点且带作答证据", actual=f"{len(points)} 个")

        # 复习任务：错题应生成待复习
        status, body = c.get(f"/api/quiz/reviews?subject_id={subject_id}&status=PENDING",
                             token=token)
        reviews = body.get("data") or []
        has_review = len(reviews) > 0
        rep.case(suite, f"review-{stu['key']}", has_review == exp["has_review"],
                 f"待复习 {len(reviews)} 条",
                 expected=f"有复习任务={exp['has_review']}", actual=f"{has_review}")

    # 教师端学情与原始记录一致
    status, body = c.get(f"/api/teacher/classes/{klass['id']}/analytics?days=30",
                         token=ctx["teacher"])
    stats = body.get("data") or {}
    raw = _raw_attempt_counts(subject_id)
    rep.case(suite, "analytics-sample-size",
             stats.get("participation", {}).get("attempt_count") == raw["attempts"],
             f"学情样本量 {stats.get('participation', {}).get('attempt_count')} vs "
             f"原始记录 {raw['attempts']}",
             expected=f"{raw['attempts']} 次",
             actual=f"{stats.get('participation', {}).get('attempt_count')} 次",
             extra={"raw": raw})
    rep.case(suite, "analytics-source-labelled",
             bool(stats.get("sources")) and bool(stats.get("range")),
             f"来源与时间范围已标注：{stats.get('sources')} / {stats.get('range')}",
             expected="标注来源与时间范围", actual=str(stats.get("range")))

    # 重复提交：同 token 幂等；重做后按最新一次判定
    rp = spec["repeat_case"]
    token = ctx["students"][rp["student"]]
    info = option_of[rp["question_id"]]
    before = _raw_attempt_counts(subject_id)
    c.post("/api/quiz/answer", {"question_id": info["question"]["id"],
                                "user_answer": info["wrong"],
                                "client_token": f"repeat-{_RUN_ID}-{rp['question_id']}-1"},
           token=token)
    mid = _raw_attempt_counts(subject_id)
    rep.case(suite, "repeat-raw-count-increases", mid["attempts"] == before["attempts"] + 1,
             f"重做后原始作答 {before['attempts']} → {mid['attempts']}",
             expected="原始计数 +1", actual=f"+{mid['attempts'] - before['attempts']}")

    # 同一 client_token 重复提交 → 不重复计数
    c.post("/api/quiz/answer", {"question_id": info["question"]["id"],
                                "user_answer": info["correct"],
                                "client_token": f"repeat-idem-{_RUN_ID}"},
           token=token)
    after_first = _raw_attempt_counts(subject_id)
    c.post("/api/quiz/answer", {"question_id": info["question"]["id"],
                                "user_answer": info["correct"],
                                "client_token": f"repeat-idem-{_RUN_ID}"},
           token=token)
    after_second = _raw_attempt_counts(subject_id)
    rep.case(suite, "repeat-idempotent-token",
             after_first["attempts"] == after_second["attempts"],
             f"同一 client_token 重复提交：{after_first['attempts']} → "
             f"{after_second['attempts']}",
             expected="不重复计数", actual=f"+{after_second['attempts'] - after_first['attempts']}")

    status, body = c.get(f"/api/teacher/classes/{klass['id']}/analytics?days=30",
                         token=ctx["teacher"])
    kp = ((body.get("data") or {}).get("knowledge_points") or [{}])[0]
    rep.case(suite, "repeat-latest-attempt-basis",
             kp.get("basis") == "每题最新一次作答" and "raw_attempt_count" in kp,
             f"知识点统计口径={kp.get('basis')}，原始计数={kp.get('raw_attempt_count')}",
             expected="按每题最新一次，并保留原始计数",
             actual=str({k: kp.get(k) for k in ("attempt_count", "raw_attempt_count",
                                                "wrong_count", "raw_wrong_count")}))
    return assignment, option_of


def _raw_attempt_counts(subject_id: str) -> Dict[str, int]:
    from services import quiz_service
    conn = quiz_service._get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS attempts, COUNT(DISTINCT user_id || '|' || question_id) AS distinct_pairs "
            "FROM quiz_attempts WHERE subject_id=?", (subject_id,)).fetchone()
        return {"attempts": row["attempts"], "distinct_pairs": row["distinct_pairs"]}
    finally:
        conn.close()


# ==================== 3) Agent ====================

def run_agent(c: Client, ctx: Dict[str, Any], ds: dict, rep: Report):
    suite = "agent"
    spec = ds["agent"]
    token = ctx["students"]["stu_mid"]     # 有错题的学生
    status, body = c.post("/api/agent/run",
                          {"goal": spec["goal"], "subject_id": ctx["course"]["subject_id"]},
                          token=token)
    if status != 200 or not isinstance(body, dict):
        rep.case(suite, "agent-run", False, f"运行失败 {status}", actual=str(body)[:200])
        return
    run_id = (body.get("data") or {}).get("run_id") or (body.get("data") or {}).get("id")
    # 等待结束（Agent 是异步的）
    final = None
    for _ in range(60):
        status, detail = c.get(f"/api/agent/runs/{run_id}", token=token)
        final = (detail or {}).get("data") or {}
        if final.get("status") in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)
    final = final or {}
    blob = json.dumps(final, ensure_ascii=False)
    rep.case(suite, "agent-run-status", final.get("status") == "SUCCEEDED",
             f"运行状态={final.get('status')}", expected="SUCCEEDED",
             actual=str(final.get("status")))

    # 以**工具调用记录**为客观依据（界面文案可能改，工具名是稳定的机器证据）
    steps = final.get("steps_detail") or []
    tools = [str(s.get("tool") or s.get("tool_name") or "") for s in steps]
    summaries = " ".join(str(s.get("summary") or "") for s in steps)
    rep.case(suite, "agent-tools-called", bool(tools),
             f"调用工具 {len(tools)} 个：{tools}",
             expected="有工具调用记录", actual=str(tools))
    for tool in spec.get("expect_tools", []):
        ok = tool in tools
        rep.case(suite, f"agent-tool-{tool}", ok,
                 f"{'调用了' if ok else '未调用'} {tool}",
                 expected=f"必须调用 {tool}",
                 actual=f"实际工具：{tools}")
    # 读取到的学生数据必须是真实的（错题/复习条数来自该生记录）
    rep.case(suite, "agent-reads-real-records",
             bool(summaries) and any(k in summaries for k in ("错题", "待复习", "知识点")),
             f"学习状态摘要：{summaries[:120]}",
             expected="包含该生真实错题/复习/知识点数据", actual=summaries[:160])
    # 涉及计划变更必须走提议通道
    proposal_tools = [t for t in tools if "propose" in t or "adjust" in t]
    rep.case(suite, "agent-changes-via-proposal",
             (not proposal_tools) or any("propose" in t for t in proposal_tools),
             f"与计划变更相关的工具：{proposal_tools or '无'}",
             expected="改计划走 propose_* 提议通道（待学生确认），不静默改期",
             actual=str(proposal_tools))

    # 是否引用了该学生的真实数据（错题/知识点）
    mentions = [kw for kw in spec["expect_mentions_student_data"] if kw in blob]
    rep.case(suite, "agent-uses-student-data", bool(mentions),
             f"建议中出现的课程相关词：{mentions}", expected="结合该生的错题/进度",
             actual=blob[:200])

    # 改计划必须走确认机制：重大调整不得自动应用
    status, body = c.post("/api/plan/adjustments/propose",
                          {"subject_id": ctx["course"]["subject_id"]}, token=token)
    if status != 200:
        rep.case(suite, "plan-confirmation", False, f"生成调整方案失败 {status}",
                 actual=str(body)[:200])
    else:
        proposal = (body.get("data") or {})
        requires = bool(proposal.get("requires_confirmation"))
        major = bool(proposal.get("major"))
        # 未确认时不应生效：状态应仍是 PROPOSED
        status_after = proposal.get("status")
        ok = (not major) or (requires and status_after == "PROPOSED")
        rep.case(suite, "plan-confirmation", ok,
                 f"重大调整={major} 需确认={requires} 状态={status_after}",
                 expected="重大调整必须待确认，不自动应用",
                 actual=f"status={status_after}")
    rep.suite(suite)["notes"].append(
        "Agent 建议的措辞由模型生成，本项只验证「是否引用了该生的真实记录」与"
        "「改计划是否走确认机制」，不评判措辞质量。")


# ==================== 4) 语音 ====================

def run_voice(c: Client, ctx: Dict[str, Any], ds: dict, rep: Report):
    suite = "voice"
    LISTEN_DIR.mkdir(parents=True, exist_ok=True)
    status, health = c.get("/api/health")
    caps = ((health or {}).get("data") or {}).get("capabilities", {})
    asr_ok = bool((caps.get("local_asr") or {}).get("available"))
    tts_ok = bool((caps.get("local_tts") or {}).get("available"))
    rep.case(suite, "speech-capability", asr_ok and tts_ok,
             f"本地 ASR={asr_ok} TTS={tts_ok}",
             expected="本地 ASR/TTS 均可用",
             actual=str({k: (caps.get(k) or {}).get("reason") for k in ("local_asr", "local_tts")}))
    if not (asr_ok and tts_ok):
        rep.suite(suite)["limits"].append(
            "本地语音服务不可用，语音链路未评测（文字功能不受影响）")
        return

    token = ctx["students"]["stu_good"]
    for s in ds["voice"]["samples"]:
        # 合成 → 保存音频 → 再识别回来（可懂度检查）
        t0 = time.time()
        status, body = c.post("/api/audio/tts", {"text": s["text"]}, token=token)
        synth_s = round(time.time() - t0, 2)
        if status != 200:
            rep.case(suite, f"tts-{s['id']}", False, f"合成失败 {status}",
                     actual=str(body)[:160])
            continue
        items = ((body.get("data") or {}).get("items") or [])
        segs = len(items)
        audio = b""
        for it in items:
            import base64
            audio += base64.b64decode(it["audio_b64"])
        wav_path = LISTEN_DIR / f"{s['id']}_{s['kind']}.wav"
        wav_path.write_bytes(audio)
        duration = _wav_seconds(audio)
        rep.case(suite, f"tts-{s['id']}", segs >= 1 and duration > 0,
                 f"合成 {segs} 段 / {duration:.2f}s 音频 / 用时 {synth_s}s "
                 f"（RTF={round(synth_s / duration, 2) if duration else 'n/a'}）",
                 expected="返回音频且时长>0", actual=f"{segs} 段",
                 extra={"synth_seconds": synth_s, "audio_seconds": duration,
                        "segments": segs, "sample": str(wav_path)})

        # 识别回读（用刚合成的音频当输入）
        t1 = time.time()
        status, body = c.post("/api/audio/transcribe",
                              raw=_asr_multipart(audio), token=token,
                              content_type=_asr_boundary())
        asr_s = round(time.time() - t1, 2)
        text = ((body or {}).get("data") or {}).get("text") if status == 200 else None
        ok = status == 200 and bool(text)
        rep.case(suite, f"asr-roundtrip-{s['id']}", ok,
                 f"识别用时 {asr_s}s，回读：{(text or '')[:60]}",
                 expected="能识别出文本", actual=(text or str(body))[:80],
                 extra={"asr_seconds": asr_s, "transcript": text,
                        "source_text": s["text"]})

        # 完整链路：提问音频 → 识别 → 问答
        if s["kind"] == "question" and text:
            t2 = time.time()
            status, body = c.post("/api/chat",
                                  {"question": text, "subject_id": ctx["course"]["subject_id"]},
                                  token=token)
            qa_s = round(time.time() - t2, 2)
            answer = (body or {}).get("answer", "") if status == 200 else ""
            rep.case(suite, f"end2end-{s['id']}", status == 200 and len(str(answer)) > 10,
                     f"识别→问答 用时 {qa_s}s，回答 {len(str(answer))} 字",
                     expected="得到文字回答", actual=str(answer)[:80],
                     extra={"qa_seconds": qa_s})

    # 重复测量：单次耗时会被瞬时 GPU 争用污染（首轮全量评测就出现过 RTF 17 的离群值，
    # 单独复测为 0.90-1.00），因此给出 min/中位/max 区间，而不是只报一个数。
    repeats = []
    probe_text = ds["voice"]["samples"][1]["text"]
    for _ in range(3):
        t0 = time.time()
        status, body = c.post("/api/audio/tts", {"text": probe_text}, token=token)
        dt = round(time.time() - t0, 2)
        if status != 200:
            break
        dur = _wav_seconds(b"".join(
            __import__("base64").b64decode(i["audio_b64"])
            for i in (body.get("data") or {}).get("items") or []))
        repeats.append({"seconds": dt, "audio_seconds": dur,
                        "rtf": round(dt / dur, 2) if dur else None})
    if repeats:
        rtfs = sorted(r["rtf"] for r in repeats if r["rtf"])
        rep.case(suite, "tts-latency-repeat", True,
                 f"同一文本重复 3 次合成：RTF 最小 {rtfs[0]} / 中位 {rtfs[1]} / 最大 {rtfs[-1]}"
                 f"，绝对耗时 {[r['seconds'] for r in repeats]}s",
                 expected="给出可重复的耗时区间（而非单次值）",
                 actual=str(repeats),
                 extra={"repeats": repeats,
                        "rtf_min": rtfs[0], "rtf_median": rtfs[1], "rtf_max": rtfs[-1]})

    rep.suite(suite)["limits"].append(
        "本项只验证流程与耗时；**音质/自然度必须人工试听**（样本见 output/listen/），"
        "程序跑通不代表听感合格")
    rep.suite(suite)["limits"].append(
        "耗时会被 GPU 争用显著放大：首轮全量评测中同一接口出现 RTF 17.3 的离群值，"
        "单独复测为 0.90-1.00；因此以 tts-latency-repeat 的区间为准，"
        "并建议评测时不要让其他 GPU 任务同时运行")
    rep.suite(suite)["notes"].append(
        "识别回读文本可用于粗查漏读/错读；同音字与断句仍需人工确认")


def _asr_boundary() -> str:
    return "multipart/form-data; boundary=----evalaudio"


def _asr_multipart(audio: bytes) -> bytes:
    b = "----evalaudio"
    return (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"eval.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode() + \
        audio + f"\r\n--{b}--\r\n".encode()


def _wav_seconds(audio: bytes) -> float:
    try:
        import io
        with wave.open(io.BytesIO(audio), "rb") as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 2)
    except Exception:
        return 0.0


# ==================== 报告输出 ====================

def write_reports(rep: Report, ctx: Dict[str, Any], args) -> Dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summary = rep.summary()
    meta = {
        "started_at": rep.started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "backend": args.base,
        "data_scope": "隔离数据目录（评测账号，非真实学生）",
        "suites": args.suites,
        "kb_ok": ctx.get("kb_ok"),
        "course_subject_id": (ctx.get("course") or {}).get("subject_id"),
        "students": list((ctx.get("students") or {}).keys()),
    }
    report = {"meta": meta, "summary": summary}
    (OUTPUT / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 教学效果评测报告（自动生成）", "",
        f"- 运行时间：{meta['started_at']} → {meta['finished_at']}",
        f"- 后端：`{meta['backend']}`",
        f"- 数据范围：**{meta['data_scope']}**",
        f"- 评测课程学科：`{meta['course_subject_id']}`，知识库可用={meta['kb_ok']}",
        "",
        "> 说明：以下结果来自**隔离的评测账号**，用于验证功能链路与可复现性；",
        "> **不等于真实学生的学习成效**。真实教学效果仍标注为「待真实试用验证」。",
        "",
        "## 一、通过率总览", "",
        "| 评测集 | 样本数 | 通过 | 失败 | 通过率 |",
        "|---|---|---|---|---|",
    ]
    for name, s in summary.items():
        lines.append(f"| {name} | {s['sample_count']} | {s['passed']} | "
                     f"{s['failed']} | {s['pass_rate']} |")
    lines.append("")
    for name, s in summary.items():
        lines.append(f"## {name}")
        lines.append("")
        if s["notes"]:
            for n in s["notes"]:
                lines.append(f"- 说明：{n}")
            lines.append("")
        if s["failures"]:
            lines.append("**失败案例**：")
            lines.append("")
            lines.append("| 用例 | 期望 | 实际 |")
            lines.append("|---|---|---|")
            for f in s["failures"]:
                lines.append(f"| {f['id']} | {f.get('expected','')} | "
                             f"{(f.get('actual') or '')[:120]} |")
            lines.append("")
        else:
            lines.append("无失败案例。")
            lines.append("")
        if s["limits"]:
            lines.append("**已知限制**：")
            lines.append("")
            for lim in s["limits"]:
                lines.append(f"- {lim}")
            lines.append("")
        lines.append("<details><summary>全部用例</summary>")
        lines.append("")
        lines.append("| 用例 | 结果 | 详情 |")
        lines.append("|---|---|---|")
        for case in rep.suites[name]["cases"]:
            lines.append(f"| {case['id']} | {'PASS' if case['ok'] else 'FAIL'} | "
                         f"{case.get('detail','')} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    (OUTPUT / "EVAL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="教学效果评估")
    parser.add_argument("--base", default=os.getenv("EVAL_BASE", "http://127.0.0.1:8001"))
    parser.add_argument("--suites", default="rag,loop,agent,voice")
    parser.add_argument("--suffix", default=None,
                        help="评测学生账号后缀（默认按运行时间生成，保证每轮状态干净）")
    parser.add_argument("--with-distractor", action="store_true", default=True,
                        help="额外跑一轮：加入无关资料后是否仍能正确回答")
    args = parser.parse_args()
    args.suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    global _RUN_ID
    _RUN_ID = args.suffix or datetime.now().strftime("%m%d%H%M")
    args.suffix = _RUN_ID

    # ---- 第一件事：隔离闸门（早于任何 HTTP 调用；不满足则直接退出，不写任何数据）----
    from tests._isolation_guard import require_isolated
    guard_state = require_isolated(
        "tests/eval/run_eval.py",
        "教学效果评测（建立评测账号/课程/题目/作答，全部写入该目录）")

    ds = json.loads((DATASET / "eval_set.json").read_text(encoding="utf-8"))
    c = Client(args.base)

    # 安全闸门：必须运行在隔离数据目录上
    status, health = c.get("/api/health")
    if status != 200:
        print(f"后端不可用：{args.base}（{status}）")
        return 2
    scope = ((health or {}).get("data") or {}).get("data_scope") or {}
    if not scope.get("isolated"):
        print("拒绝执行：后端未运行在隔离数据目录（EDU_DATA_DIR）。"
              "评测会建账号与课程，不能写正式库。")
        return 2

    # （隔离闸门已在 main() 最前面执行，此处只做目录一致性核对）
    from config.path_config import get_data_dir
    script_dir = get_data_dir()
    backend_dir = Path(scope.get("dir") or "") if scope.get("dir") else None
    backend_name = scope.get("dir_name")
    if backend_name and script_dir.name != backend_name:
        print(f"拒绝执行：本脚本数据目录（{script_dir.name}）与后端（{backend_name}）不一致。\n"
              f"评测脚本会直接读隔离库做核对，必须指向同一目录。请这样运行：\n"
              f"  EDU_DATA_DIR=<与后端相同的目录> python tests/eval/run_eval.py "
              f"--base {args.base}")
        return 2
    print(f"后端就绪，隔离目录：{backend_name}（脚本目录 {script_dir.name} 一致）")

    rep = Report()
    ctx = bootstrap(c, args.suffix)
    print(f"评测课程：{ctx['course']['id']} / 班级 {ctx['class']['id']}"
          f"（学生 {len(ctx['students'])} 名）")

    if "rag" in args.suites:
        print("\n=== RAG 评测 ===")
        run_rag(c, ctx, ds, rep)
        if args.with_distractor:
            print("\n=== RAG 评测（加入无关资料后） ===")
            run_rag_with_distractor(c, ctx, ds, rep)
    if "loop" in args.suites:
        print("\n=== 教学闭环评测 ===")
        run_loop(c, ctx, ds, rep)
    if "agent" in args.suites:
        print("\n=== Agent 评测 ===")
        run_agent(c, ctx, ds, rep)
    if "voice" in args.suites:
        print("\n=== 语音评测 ===")
        run_voice(c, ctx, ds, rep)

    report = write_reports(rep, ctx, args)
    print("\n=== 汇总 ===")
    for name, s in report["summary"].items():
        print(f"  {name}: {s['passed']}/{s['sample_count']} 通过"
              f"（通过率 {s['pass_rate']}）")
    print(f"\n报告：{OUTPUT / 'EVAL_REPORT.md'}")
    print(f"      {OUTPUT / 'eval_report.json'}")
    failed = sum(s["failed"] for s in report["summary"].values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
