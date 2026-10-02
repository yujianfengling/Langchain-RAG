"""
真实自测服务（阶段一）

职责：
- 题库持久化：题目、选项（带稳定选项 ID）、服务端标准答案
- 题库/作答分离：测验模式下发给前端的题目不含标准答案
- 后端权威判分：只接受"用户所选选项 ID"，由服务端比对标准答案
- 作答记录：每次作答、得分、用时、是否看提示/看答案、第几次
- 幂等：同一 client_token 重复提交不重复计分，也不重复产生复习任务
- 简答题：无客观评分时标记 pending_self_eval，支持用户自评

不做：不伪造客观评分；不接受前端传入的 is_correct。
"""

import hashlib
import json
import re
import sqlite3
from typing import List, Dict, Any, Optional

from utils.logger import logger
from services.ai_service import call_llm
from services.learning_data import (
    _get_db,
    _now,
    _new_id,
    ensure_knowledge_point,
    QUESTION_TYPES,
    ATTEMPT_KINDS,
    GRADE_GRADED,
    GRADE_PENDING_SELF,
    GRADE_AI_GRADED,
)

# 选项 ID 序列（单选）
_CHOICE_IDS = ("A", "B", "C", "D", "E", "F")
# 判断题选项 ID
_JUDGE_TRUE = "T"
_JUDGE_FALSE = "F"


class QuizServiceError(Exception):
    """自测服务异常（携带 error_type）"""

    def __init__(self, message: str, error_type: str = "unknown"):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


# ==================== 简答题 AI 判分 ====================

# 简答题判分 Prompt：只依据题目的参考答案/解析判断，不引入外部知识
SHORT_ANSWER_GRADER_PROMPT = """你是一名严谨的阅卷老师，请判断学生的简答题作答是否达到参考答案的要求。

## 题目
{stem}

## 参考答案
{reference_answer}

## 参考解析
{explanation}

## 学生作答
{user_answer}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "is_correct": true 或 false,
  "reason": "20-60 字的判分理由，指出答对的关键点或遗漏、错误的要点"
}}

## 判分规则
1. 只依据上面的参考答案与解析判断，不要引入任何额外知识。
2. 语义等价、要点齐全算正确；表述不完整但核心要点正确也算正确。
3. 遗漏关键要点或出现明显错误算错误。
4. 实在无法判断时判为 false，并在 reason 里说明原因。
"""


def _parse_grader_json(text: str) -> Dict[str, Any]:
    """解析 AI 判分输出（兼容 markdown 代码块与前后夹带的文字）"""
    if not text:
        return {}
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    candidates = [stripped]
    m = re.search(r"\{[\s\S]*\}", stripped)
    if m:
        candidates.append(m.group())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


def grade_short_answer_with_ai(
    stem: str,
    reference_answer: Optional[str],
    explanation: Optional[str],
    user_answer: str,
) -> Optional[Dict[str, Any]]:
    """
    用模型评判简答题作答（不伪装成客观判分）。

    Returns:
        {"is_correct": bool, "reason": str}；无判分依据或模型不可用时返回 None，
        由调用方降级为 pending_self_eval —— 宁可让用户自评，也不伪造判分。
    """
    ref = (reference_answer or "").strip()
    exp = (explanation or "").strip()
    ans = (user_answer or "").strip()
    if not ans or not (ref or exp):
        return None
    prompt = SHORT_ANSWER_GRADER_PROMPT.format(
        stem=stem, reference_answer=ref or "（无）", explanation=exp or "（无）", user_answer=ans)
    try:
        raw = call_llm(prompt, system_message="你是智慧教育阅卷助手，只输出 JSON。")
    except Exception as e:
        logger.warning(f"简答题 AI 判分调用失败（降级为待自评）: {e}")
        return None
    data = _parse_grader_json(raw)
    if not isinstance(data, dict) or "is_correct" not in data:
        logger.warning(f"简答题 AI 判分输出无法解析（降级为待自评）: {str(raw)[:120]}")
        return None
    return {
        "is_correct": bool(data.get("is_correct")),
        "reason": str(data.get("reason") or "").strip(),
    }


def to_public(row: Any) -> Dict[str, Any]:
    """
    把题库记录转换为"可下发给前端"的题目结构（**不含标准答案与解析**）。

    字段说明：
    - id: 服务端题目 ID（前端提交作答时携带）
    - question / type: 题干与题型（沿用既有前端字段名，避免契约漂移）
    - options: [{id, text}]，选项 ID 用于权威判分
    - 标准答案只在"提交作答后"或"显式查看答案"时返回，避免答案随题目批量下发
    """
    if isinstance(row, dict):
        options = row.get("options") or []
        version = row.get("version") or 1
        source = row.get("source")
    else:
        options = json.loads(row["options_json"] or "[]")
        version = row["version"] or 1
        source = row["source"]
    return {
        "id": row["id"],
        "question": row["stem"],
        "type": row["question_type"],
        "options": options,
        "knowledge_point": row["knowledge_point_name"],
        "knowledge_point_id": row["knowledge_point_id"],
        "version": version,
        "server_persisted": True,
        # 题目来源：knowledge_base(你的资料) / web_search(网络资料) /
        # general_knowledge(AI 通用知识) / note / quiz。前端据此标注，避免
        # 把通用知识出的题误当成"我上传的资料里有这道题"。
        "source": source,
    }


# ==================== 选项与答案构建 ====================

def _normalize_text(text: str) -> str:
    if text is None:
        return ""
    return " ".join(str(text).split()).strip().strip("。．.、，,；;：:")


def build_options(qtype: str, raw_options: Any) -> List[Dict[str, str]]:
    """
    规范化选项：为每个选项分配稳定 ID。

    - judgement → T/F（正确/错误）
    - single_choice → A/B/C/D...
    - short_answer → []
    """
    if qtype == "judgement":
        return [{"id": _JUDGE_TRUE, "text": "正确"}, {"id": _JUDGE_FALSE, "text": "错误"}]
    if qtype != "single_choice":
        return []
    options = raw_options if isinstance(raw_options, list) else []
    built: List[Dict[str, str]] = []
    for i, opt in enumerate(options):
        if i >= len(_CHOICE_IDS):
            break
        text = _normalize_text(opt)
        if not text:
            continue
        # 若选项自带 "A. xxx" 前缀，剥掉前缀避免显示重复
        text = re.sub(r"^[A-Fa-f][\.、\)）]\s*", "", text).strip()
        built.append({"id": _CHOICE_IDS[i], "text": text})
    return built


def map_answer_to_option(
    qtype: str,
    options: List[Dict[str, str]],
    answer_text: str,
) -> Dict[str, Any]:
    """
    将模型给出的答案文本映射为选项 ID（服务端权威答案）。

    Returns:
        {"correct_option_id": str|None, "reference_answer": str, "ambiguous": bool}
    """
    raw = _normalize_text(answer_text)
    if qtype == "short_answer":
        return {"correct_option_id": None, "reference_answer": str(answer_text or "").strip(),
                "ambiguous": False}

    if not options:
        return {"correct_option_id": None, "reference_answer": raw, "ambiguous": True}

    ids = [o["id"] for o in options]
    upper = raw.upper()

    # 1) 答案本身就是选项 ID（"A" / "T" / "F"）
    if upper in ids:
        return {"correct_option_id": upper, "reference_answer": raw, "ambiguous": False}

    # 2) "A. xxx" / "A、xxx" / "A) xxx"
    m = re.match(r"^([A-Fa-fTt])\s*[\.、\)）]", raw)
    if m:
        cid = m.group(1).upper()
        if cid in ids:
            return {"correct_option_id": cid, "reference_answer": raw, "ambiguous": False}

    # 3) 判断题文字
    if qtype == "judgement":
        if raw in ("正确", "对", "是", "true", "True", "TRUE", "√"):
            return {"correct_option_id": _JUDGE_TRUE, "reference_answer": raw, "ambiguous": False}
        if raw in ("错误", "错", "否", "false", "False", "FALSE", "×"):
            return {"correct_option_id": _JUDGE_FALSE, "reference_answer": raw, "ambiguous": False}

    # 4) 与选项文本完全一致
    for o in options:
        if _normalize_text(o["text"]) == raw:
            return {"correct_option_id": o["id"], "reference_answer": raw, "ambiguous": False}

    # 5) 唯一包含匹配（避免多个选项都命中）
    matched = [o["id"] for o in options if raw and raw in _normalize_text(o["text"])]
    if len(matched) == 1:
        return {"correct_option_id": matched[0], "reference_answer": raw, "ambiguous": False}

    # 无法可靠映射 → 不伪造客观答案，标记为需自评
    return {"correct_option_id": None, "reference_answer": raw, "ambiguous": True}


def _content_hash(subject_id: str, qtype: str, stem: str) -> str:
    base = f"{subject_id}|{qtype}|{_normalize_text(stem)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ==================== 题库持久化 ====================

def persist_questions(
    subject_id: str,
    knowledge_point_name: str,
    questions: List[Dict[str, Any]],
    source: str = "note",
    kp_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    持久化题目并返回"可发给前端"的题目（不含标准答案）。

    同一学科内题干相同视为同一题（复用已有 ID 与版本），避免重复累积。

    Returns:
        可下发给前端的题目列表（不含标准答案/解析），见 to_public()
    """
    if not subject_id or not questions:
        return []
    kp_id = kp_id or ensure_knowledge_point(subject_id, knowledge_point_name, source=source)
    out: List[Dict[str, Any]] = []
    conn = _get_db()
    try:
        for q in questions:
            if not isinstance(q, dict):
                continue
            qtype = str(q.get("type", "")).strip() or "single_choice"
            if qtype not in QUESTION_TYPES:
                qtype = "single_choice"
            stem = str(q.get("question") or q.get("stem") or "").strip()
            if not stem:
                continue
            options = build_options(qtype, q.get("options"))
            mapping = map_answer_to_option(qtype, options, q.get("answer", ""))
            chash = _content_hash(subject_id, qtype, stem)
            now = _now()
            row = conn.execute(
                "SELECT * FROM quiz_questions WHERE subject_id=? AND content_hash=?",
                (subject_id, chash)).fetchone()
            if row:
                qid = row["id"]
                version = row["version"]
            else:
                qid = _new_id("q")
                version = 1
                conn.execute(
                    """INSERT INTO quiz_questions
                       (id, subject_id, knowledge_point_id, knowledge_point_name, question_type,
                        stem, options_json, correct_option_id, reference_answer, explanation,
                        version, source, content_hash, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (qid, subject_id, kp_id, knowledge_point_name, qtype, stem,
                     json.dumps(options, ensure_ascii=False),
                     mapping["correct_option_id"], mapping["reference_answer"],
                     str(q.get("explanation", "") or "").strip(),
                     version, source, chash, now))
                conn.commit()
            out.append(to_public({
                "id": qid,
                "question_type": qtype,
                "stem": stem,
                "options": options,
                "knowledge_point_name": knowledge_point_name,
                "knowledge_point_id": kp_id,
                "version": version,
            }))
        return out
    except sqlite3.Error as e:
        logger.error(f"题库持久化失败: {e}", exc_info=True)
        raise QuizServiceError("题库保存失败", "database_error")
    finally:
        conn.close()


def get_question_public(question_id: str) -> Optional[Dict[str, Any]]:
    """获取题目（不含标准答案），供测验作答使用"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM quiz_questions WHERE id=?", (question_id,)).fetchone()
        if not row:
            return None
        item = to_public(row)
        item["subject_id"] = row["subject_id"]
        return item
    finally:
        conn.close()


def get_question_admin(question_id: str) -> Optional[Dict[str, Any]]:
    """获取题目（含标准答案），仅服务端内部使用"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM quiz_questions WHERE id=?", (question_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ==================== 查看答案（会标记为非独立作答） ====================

def reveal_answer(question_id: str, user_id: str) -> Dict[str, Any]:
    """
    主动查看答案：返回标准答案与解析，并在事件流中记录"已看答案"。

    注意：查看答案后再次提交的作答会被标记为 after_reveal，
    不作为独立掌握证据（避免"看过答案再答对"被算作真实掌握）。
    """
    q = get_question_admin(question_id)
    if not q:
        raise QuizServiceError("题目不存在", "not_found")
    try:
        from services.learning_events import emit_event
        emit_event(
            event_type="quiz_answer_revealed",
            idempotency_key=f"reveal:{user_id}:{question_id}",
            user_id=user_id,
            subject_id=q["subject_id"],
            payload={"question_id": question_id},
        )
    except Exception as e:
        logger.warning(f"记录查看答案事件失败（不影响返回）: {e}")

    return {
        "question_id": question_id,
        "correct_option_id": q["correct_option_id"],
        "reference_answer": q["reference_answer"],
        "explanation": q["explanation"],
    }


def has_revealed(user_id: str, question_id: str) -> bool:
    """判断该用户是否已查看过该题答案"""
    try:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT 1 FROM learning_events WHERE idempotency_key=? AND status='DONE'",
                (f"reveal:{user_id}:{question_id}",)).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


# ==================== 作答提交（后端权威判分） ====================

def submit_attempt(
    user_id: str,
    question_id: str,
    user_answer: Optional[str],
    client_token: Optional[str] = None,
    subject_id: Optional[str] = None,
    source_task_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    duration_sec: Optional[int] = None,
    used_hint: bool = False,
) -> Dict[str, Any]:
    """
    提交作答并由服务端判分。

    Args:
        user_id: 当前用户（由后端身份机制取得）
        question_id: 题目 ID
        user_answer: 用户所选选项 ID（单选/判断）；简答题为文本
        client_token: 客户端幂等令牌（同一次提交重复请求只计一次）
        subject_id: 学科（缺省取题目所属学科）
        source_task_id: 来源任务（从学习计划进入时携带）
        conversation_id: 来源会话
        duration_sec: 作答用时
        used_hint: 是否使用了提示

    Returns:
        判分结果（含正确答案与解析，仅在提交后返回）
    """
    q = get_question_admin(question_id)
    if not q:
        raise QuizServiceError("题目不存在", "not_found")

    subject_id = subject_id or q["subject_id"]
    qtype = q["question_type"]
    options = json.loads(q["options_json"] or "[]")

    # 归属校验（阶段一补验）：来源任务/会话必须属于当前用户，
    # 否则作答会被挂到他人资源上（越权写入 + 污染他人统计）。
    if source_task_id:
        from services import learning_service
        task = learning_service.get_task(source_task_id)
        if not task or (task.get("user_id") and task["user_id"] != user_id):
            raise QuizServiceError("来源任务不存在或不属于当前用户", "forbidden")
    if conversation_id:
        from services import conversation_service
        owner = conversation_service.get_conversation_owner(conversation_id)
        if owner is None:
            raise QuizServiceError("来源会话不存在", "not_found")
        if owner and owner != user_id:
            raise QuizServiceError("来源会话不属于当前用户", "forbidden")

    conn = _get_db()
    try:
        # 幂等：同一 client_token 直接返回既有结果（不重复计分/不重复建复习任务）
        if client_token:
            dup = conn.execute(
                "SELECT * FROM quiz_attempts WHERE user_id=? AND client_token=?",
                (user_id, client_token)).fetchone()
            if dup:
                return _attempt_result(dup, q)

        # 第几次作答
        prior = conn.execute(
            "SELECT COUNT(*) AS c FROM quiz_attempts WHERE user_id=? AND question_id=?",
            (user_id, question_id)).fetchone()["c"]
        attempt_no = prior + 1

        # 作答性质：首次且未看答案 → 独立作答；看过答案 → after_reveal；否则复习重做
        revealed = has_revealed(user_id, question_id)
        if revealed:
            attempt_kind = "after_reveal"
        elif prior == 0:
            attempt_kind = "independent"
        else:
            attempt_kind = "review"
        if attempt_kind not in ATTEMPT_KINDS:
            attempt_kind = "independent"

        # ---- 参数校验（程序执行，不依赖前端） ----
        # 客观题的作答必须是本题选项 ID 之一；否则属于无效请求，
        # 不能悄悄记成"答错"（那会污染错题本与掌握度）
        if qtype in ("single_choice", "judgement") and options:
            valid_ids = {(o.get("id") or "").upper() for o in options}
            submitted = (user_answer or "").strip().upper()
            if submitted and submitted not in valid_ids:
                raise QuizServiceError(
                    f"无效选项：{user_answer}（本题可选：{'/'.join(sorted(valid_ids))}）",
                    "invalid_request")

        # ---- 服务端判分 ----
        correct_option_id = q["correct_option_id"]
        grade_status = GRADE_GRADED
        is_correct: Optional[int] = None
        user_answer_text = None
        ai_feedback: Optional[str] = None

        if qtype == "short_answer":
            # 简答题：有参考答案/解析时交给 AI 判分（记为 ai_graded，不冒充客观判分）；
            # 没有判分依据或模型不可用时降级为待自评。
            # 用户之后仍可用"我答对了 / 我答错了"覆盖 AI 判定（见 self_evaluate）。
            user_answer_text = (user_answer or "").strip()
            judged = grade_short_answer_with_ai(
                q["stem"], q["reference_answer"], q["explanation"], user_answer_text or "")
            if judged is None:
                grade_status = GRADE_PENDING_SELF
            else:
                grade_status = GRADE_AI_GRADED
                is_correct = 1 if judged["is_correct"] else 0
                ai_feedback = judged.get("reason") or None
        elif correct_option_id is None:
            # 模型答案无法可靠映射为选项 → 不伪造客观判分
            grade_status = GRADE_PENDING_SELF
            user_answer_text = _option_text(options, user_answer)
        else:
            is_correct = 1 if (user_answer or "").strip().upper() == correct_option_id else 0
            user_answer_text = _option_text(options, user_answer)

        attempt_id = _new_id("att")
        now = _now()
        conn.execute(
            """INSERT INTO quiz_attempts
               (id, user_id, subject_id, question_id, knowledge_point_id, knowledge_point_name,
                attempt_no, attempt_kind, user_answer, user_answer_text, correct_option_id,
                is_correct, grade_status, ai_feedback, used_hint, revealed_answer, duration_sec,
                source_task_id, conversation_id, client_token, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_id, user_id, subject_id, question_id,
             q["knowledge_point_id"], q["knowledge_point_name"],
             attempt_no, attempt_kind, (user_answer or "").strip() or None, user_answer_text,
             correct_option_id, is_correct, grade_status, ai_feedback,
             1 if used_hint else 0, 1 if revealed else 0,
             duration_sec, source_task_id, conversation_id, client_token, now))
        conn.commit()

        row = conn.execute("SELECT * FROM quiz_attempts WHERE id=?", (attempt_id,)).fetchone()
        result = _attempt_result(row, q)
        result["attempt_kind"] = attempt_kind
        result["attempt_no"] = attempt_no
        result["grade_status"] = grade_status
    except sqlite3.Error as e:
        logger.error(f"作答保存失败: {e}", exc_info=True)
        raise QuizServiceError("作答保存失败", "database_error")
    finally:
        conn.close()

    # ---- 作答已持久化后再做联动（联动失败不影响作答记录，可重试） ----
    linkage: Dict[str, Any] = {}
    if is_correct is not None:
        try:
            from services.learning_events import emit_event
            ev = emit_event(
                event_type="quiz_attempt_recorded",
                idempotency_key=f"quiz_attempt:{result['attempt_id']}",
                user_id=user_id,
                subject_id=subject_id,
                payload={
                    "attempt_id": result["attempt_id"],
                    "question_id": question_id,
                    "knowledge_point": q["knowledge_point_name"],
                    "knowledge_point_id": q["knowledge_point_id"],
                    "is_correct": bool(is_correct),
                    "attempt_kind": attempt_kind,
                    "source_task_id": source_task_id,
                },
            )
            linkage = {"event_id": ev.get("event_id"), "event_status": ev.get("status"),
                       "handler_errors": ev.get("handler_errors", [])}
        except Exception as e:
            logger.warning(f"作答联动事件发送失败（作答已保存）: {e}")
            linkage = {"event_status": "failed_to_emit", "error": str(e)[:120]}
    else:
        linkage = {"event_status": "skipped", "reason": "待自评作答暂不进入掌握度计算"}

    result["linkage"] = linkage
    return result


def _option_text(options: List[Dict[str, str]], option_id: Optional[str]) -> Optional[str]:
    if not option_id:
        return None
    for o in options:
        if o["id"] == (option_id or "").strip().upper():
            return o["text"]
    return None


def _attempt_result(row, q) -> Dict[str, Any]:
    """构造作答结果（提交后才返回标准答案与解析）"""
    options = json.loads(q["options_json"] or "[]")
    return {
        "attempt_id": row["id"],
        "question_id": row["question_id"],
        "knowledge_point": row["knowledge_point_name"],
        "knowledge_point_id": row["knowledge_point_id"],
        "is_correct": None if row["is_correct"] is None else bool(row["is_correct"]),
        "grade_status": row["grade_status"],
        "ai_feedback": row["ai_feedback"] if "ai_feedback" in row.keys() else None,
        "user_answer": row["user_answer"],
        "user_answer_text": row["user_answer_text"],
        "correct_option_id": row["correct_option_id"],
        "correct_option_text": _option_text(options, row["correct_option_id"]),
        "reference_answer": q["reference_answer"] if q else None,
        "explanation": q["explanation"] if q else None,
        "attempt_no": row["attempt_no"],
        "attempt_kind": row["attempt_kind"],
        "created_at": row["created_at"],
    }


def self_evaluate(attempt_id: str, user_id: str, is_correct: bool) -> Dict[str, Any]:
    """
    简答题自评：把 pending_self_eval / ai_graded 的作答标记为自评结果。

    自评结果不等同客观判分，仅作为自评证据记录。

    联动（2026-10-02 补）：
    - 自评**答错** → 补发一条作答事件，让"错题 → 复习安排"联动生效。
      此前自评不参与联动，学生点「我答错了」既不会排复习，错题本之外的
      闭环也不会动。掌握度按 attempt_id 幂等，同一次作答不会被 AI 与自评
      各记一遍（AI 已判过就仍以 AI 的那条为准）。
    - 自评**答对** → **不**补发事件：自评不能证明掌握，也就不能关闭待办复习
      （与 after_reveal 不关闭待办同一原则，避免"点一下就完成复习"）。
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM quiz_attempts WHERE id=?", (attempt_id,)).fetchone()
        if not row:
            raise QuizServiceError("作答记录不存在", "not_found")
        if row["user_id"] != user_id:
            raise QuizServiceError("无权操作他人的作答记录", "forbidden")
        # 允许覆盖：pending_self_eval（无判分依据）或 ai_graded（AI 已判，用户不认同）
        if row["grade_status"] not in (GRADE_PENDING_SELF, GRADE_AI_GRADED):
            raise QuizServiceError("该作答已有判分结果，无需自评", "invalid_request")
        conn.execute(
            "UPDATE quiz_attempts SET is_correct=?, grade_status=? WHERE id=?",
            (1 if is_correct else 0, "self_evaluated", attempt_id))
        conn.commit()
        logger.info(f"简答题自评完成: attempt={attempt_id}, is_correct={is_correct}")
        snapshot = dict(row)
    finally:
        conn.close()

    # ---- 自评已持久化后再做联动（联动失败不影响自评记录，可重试） ----
    linkage: Dict[str, Any] = {"event_status": "skipped",
                               "reason": "自评答对不进入复习与掌握证据"}
    if not is_correct:
        try:
            from services.learning_events import emit_event
            ev = emit_event(
                event_type="quiz_attempt_recorded",
                # 与提交作答的幂等键区分：同一次作答的"提交"与"自评"是两次动作
                idempotency_key=f"quiz_self_eval:{attempt_id}",
                user_id=user_id,
                subject_id=snapshot["subject_id"],
                payload={
                    "attempt_id": attempt_id,
                    "question_id": snapshot["question_id"],
                    "knowledge_point": snapshot["knowledge_point_name"],
                    "knowledge_point_id": snapshot["knowledge_point_id"],
                    "is_correct": False,
                    "attempt_kind": snapshot["attempt_kind"],
                    "source_task_id": snapshot["source_task_id"],
                },
            )
            linkage = {"event_id": ev.get("event_id"), "event_status": ev.get("status"),
                       "handler_errors": ev.get("handler_errors", [])}
        except Exception as e:
            logger.warning(f"自评答错的复习联动发送失败（自评已保存）: {e}")
            linkage = {"event_status": "failed_to_emit", "error": str(e)[:120]}

    return {"attempt_id": attempt_id, "is_correct": is_correct,
            "grade_status": "self_evaluated", "linkage": linkage}


# ==================== 作答与错题查询 ====================

def list_attempts(
    user_id: str,
    subject_id: Optional[str] = None,
    question_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """查询作答历史（刷新/重启后仍可查看）"""
    conn = _get_db()
    try:
        q = "SELECT * FROM quiz_attempts WHERE user_id=?"
        p: List[Any] = [user_id]
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        if question_id:
            q += " AND question_id=?"
            p.append(question_id)
        q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        p.append(limit)
        return [dict(r) for r in conn.execute(q, p).fetchall()]
    finally:
        conn.close()


def list_wrong_questions(user_id: str, subject_id: Optional[str] = None,
                         include_answers: bool = False) -> List[Dict[str, Any]]:
    """
    错题本：按题目聚合最近一次作答状态。

    仅当"最近一次作答为错"时进入错题本；重做答对后移出列表，
    但历史作答记录保留（不删除错误过程）。

    答案隔离（阶段一补验）：**默认不下发标准答案与解析**。
    列表只用于"提醒我还有哪些错题"。学生点击"查看解析"时走
    `get_wrong_question_detail()`，该调用会在后端登记"已查看答案"，
    因此之后再答对不会被视为独立掌握证据。

    若该题此前已被显式查看过（revealed=True），答案已暴露过，
    这里就直接带上，避免重复请求。
    """
    conn = _get_db()
    try:
        q = """
        SELECT a.*, qs.stem, qs.options_json, qs.explanation, qs.reference_answer,
               qs.correct_option_id AS question_correct_option_id
        FROM quiz_attempts a
        JOIN (
            SELECT question_id, MAX(created_at || '|' || rowid) AS latest
            FROM quiz_attempts
            WHERE user_id = ?
            GROUP BY question_id
        ) l ON l.latest = (a.created_at || '|' || a.rowid)
        JOIN quiz_questions qs ON qs.id = a.question_id
        WHERE a.user_id = ?
        """
        p: List[Any] = [user_id, user_id]
        if subject_id:
            q += " AND a.subject_id = ?"
            p.append(subject_id)
        q += " ORDER BY a.created_at DESC"
        rows = conn.execute(q, p).fetchall()
        wrong: List[Dict[str, Any]] = []
        for r in rows:
            if r["is_correct"] != 0:
                continue
            options = json.loads(r["options_json"] or "[]")
            revealed = has_revealed(user_id, r["question_id"])
            expose = include_answers or revealed
            correct_id = r["question_correct_option_id"]
            item: Dict[str, Any] = {
                "question_id": r["question_id"],
                "stem": r["stem"],
                "options": options,
                "knowledge_point": r["knowledge_point_name"],
                "knowledge_point_id": r["knowledge_point_id"],
                "user_answer": r["user_answer"],
                "user_answer_text": r["user_answer_text"],
                "last_attempt_at": r["created_at"],
                "attempt_count": _attempt_count(conn, user_id, r["question_id"]),
                # AI 对用户自己作答的评判理由：不属于"标准答案"，因此不受答案隔离限制
                "ai_feedback": r["ai_feedback"] if "ai_feedback" in r.keys() else None,
                # 答案是否已下发：前端据此显示"查看解析"或直接显示答案
                "revealed": revealed,
                "answers_included": expose,
            }
            if expose:
                item.update({
                    "correct_option_id": correct_id,
                    "correct_option_text": _option_text(options, correct_id),
                    "explanation": r["explanation"],
                    "reference_answer": r["reference_answer"],
                })
            wrong.append(item)
        return wrong
    finally:
        conn.close()


def get_wrong_question_detail(user_id: str, question_id: str) -> Dict[str, Any]:
    """
    查看某道错题的答案与解析（**会登记"已查看答案"**）。

    登记后该题的后续作答会被记为 after_reveal，不计入独立掌握证据，
    因此"从错题本看到答案再答对"不会被算作真实掌握。

    仅允许查看自己错过的题（防止用任意 question_id 刷答案）。
    """
    if not user_id or not question_id:
        raise QuizServiceError("缺少必要参数", "invalid_request")

    # 1) 必须是自己的错题（最近一次作答为错）
    wrong_items = list_wrong_questions(user_id, None, include_answers=False)
    target = next((w for w in wrong_items if w["question_id"] == question_id), None)
    if not target:
        raise QuizServiceError("该题不在你的错题本中", "forbidden")

    q = get_question_admin(question_id)
    if not q:
        raise QuizServiceError("题目不存在", "not_found")

    # 2) 登记"已查看答案"（幂等；后端持久化，换客户端 / 重开页面同样生效）
    exposed = reveal_answer(question_id, user_id)

    options = json.loads(q["options_json"] or "[]")
    return {
        "question_id": question_id,
        "stem": q["stem"],
        "question_type": q["question_type"],
        "options": options,
        "knowledge_point": q["knowledge_point_name"],
        "knowledge_point_id": q["knowledge_point_id"],
        "user_answer": target.get("user_answer"),
        "user_answer_text": target.get("user_answer_text"),
        "correct_option_id": exposed.get("correct_option_id"),
        "correct_option_text": _option_text(options, exposed.get("correct_option_id")),
        "reference_answer": exposed.get("reference_answer"),
        "explanation": exposed.get("explanation"),
        "ai_feedback": target.get("ai_feedback"),
        "revealed": True,
        "reveal_recorded": True,
        "note": "已登记查看答案：该题后续作答不计入独立掌握证据",
    }


def _attempt_count(conn: sqlite3.Connection, user_id: str, question_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM quiz_attempts WHERE user_id=? AND question_id=?",
        (user_id, question_id)).fetchone()["c"]


def get_quiz_stats(user_id: str, subject_id: Optional[str] = None) -> Dict[str, Any]:
    """作答统计（只统计有客观判分/自评结果的作答）"""
    conn = _get_db()
    try:
        q = ("SELECT COUNT(*) AS total, "
             "SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) AS correct, "
             "SUM(CASE WHEN is_correct=0 THEN 1 ELSE 0 END) AS wrong, "
             "SUM(CASE WHEN grade_status='pending_self_eval' THEN 1 ELSE 0 END) AS pending "
             "FROM quiz_attempts WHERE user_id=?")
        p: List[Any] = [user_id]
        if subject_id:
            q += " AND subject_id=?"
            p.append(subject_id)
        row = conn.execute(q, p).fetchone()
        total = row["total"] or 0
        correct = row["correct"] or 0
        wrong = row["wrong"] or 0
        graded = correct + wrong
        return {
            "total_attempts": total,
            "correct": correct,
            "wrong": wrong,
            "pending_self_eval": row["pending"] or 0,
            "accuracy": round(correct / graded, 4) if graded else None,
        }
    finally:
        conn.close()


__all__ = [
    "QuizServiceError",
    "to_public",
    "build_options",
    "map_answer_to_option",
    "persist_questions",
    "get_question_public",
    "get_question_admin",
    "reveal_answer",
    "has_revealed",
    "submit_attempt",
    "self_evaluate",
    "grade_short_answer_with_ai",
    "list_attempts",
    "list_wrong_questions",
    "get_wrong_question_detail",
    "get_quiz_stats",
]
