"""课堂录音笔记（2026-10-24）

流程：音频 → 本地/兼容 ASR 转写 → 模型整理成结构化笔记 → 落库。
约束：
- ASR 不可用时如实报错，不用文本模型假装转写；
- 模型整理失败时仍保存转写与可读的兜底提纲（标记 ai_generated=false），
  不因为一次模型故障丢掉整段课堂；
- 只存当前用户、当前学科；列表不返回完整转写，详情才返回。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.path_config import get_data_dir
from services.ai_service import call_llm
from utils.logger import logger

MAX_TRANSCRIPT_FOR_MODEL = 12000


class LectureError(Exception):
    def __init__(self, message: str, error_type: str = "lecture_failed"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


def _db_path() -> str:
    return os.path.join(str(get_data_dir()), "lecture_notes.db")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    os.makedirs(get_data_dir(), exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lecture_notes (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            title TEXT NOT NULL,
            transcript TEXT NOT NULL,
            notes_json TEXT NOT NULL DEFAULT '{}',
            source_name TEXT,
            asr_service TEXT,
            asr_engine TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lecture_user "
                 "ON lecture_notes(user_id, subject_id, created_at)")
    conn.commit()
    return conn


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def _string_list(value: Any, limit: int = 12) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text[:160])
        if len(out) >= limit:
            break
    return out


def _fallback_summary(transcript: str) -> Dict[str, Any]:
    sentences = [s.strip() for s in re.split(r"[。！？\n]", transcript) if s.strip()]
    outline = sentences[:5] or [transcript[:120]]
    return {"title": "课堂笔记", "outline": outline, "key_points": [],
            "questions": [], "follow_ups": [], "ai_generated": False}


def summarize_transcript(transcript: str, model: Optional[str] = None) -> Dict[str, Any]:
    text = (transcript or "").strip()
    if not text:
        raise LectureError("转写结果为空，无法生成笔记", "asr_empty")
    prompt = f"""你是一名课堂笔记整理助手。请把下面的课堂录音转写整理成可复习的笔记。

## 转写内容
{text[:MAX_TRANSCRIPT_FOR_MODEL]}

## 输出要求（严格 JSON，不要输出任何其他内容）
{{
  "title": "这节课的标题",
  "outline": ["提纲 1", "提纲 2"],
  "key_points": ["重点 1", "重点 2"],
  "questions": ["课堂里提到、但还没解决的问题"],
  "follow_ups": ["建议课后练习或追问"]
}}

## 规则
1. 只依据转写内容，不补充老师没讲的知识；转写残缺时如实保留。
2. 每个数组 3-8 条，每条不超过 60 字。
3. 不要输出 Markdown、解释或代码块，只输出 JSON。
"""
    try:
        answer = call_llm(prompt, system_message="你是课堂笔记整理助手，只输出 JSON。",
                          model=model)
    except Exception as e:
        logger.warning(f"课堂笔记整理失败，使用兜底提纲: {type(e).__name__}: {e}")
        return _fallback_summary(text)

    raw = _extract_json(answer)
    if raw is None:
        logger.warning("课堂笔记模型输出不是 JSON，使用兜底提纲")
        return _fallback_summary(text)
    outline = _string_list(raw.get("outline"))
    key_points = _string_list(raw.get("key_points"))
    if not outline and not key_points:
        return _fallback_summary(text)
    return {
        "title": str(raw.get("title") or "课堂笔记").strip()[:80] or "课堂笔记",
        "outline": outline,
        "key_points": key_points,
        "questions": _string_list(raw.get("questions")),
        "follow_ups": _string_list(raw.get("follow_ups")),
        "ai_generated": True,
    }


def _align_knowledge_points(user_id: str, subject_id: str, transcript: str) -> List[str]:
    """把转写里出现的知识地图节点标出来；失败不影响笔记生成。"""
    try:
        from services import knowledge_map
        data = knowledge_map.map_with_progress(user_id, subject_id) or {}
        hits: List[str] = []
        for node in data.get("nodes") or []:
            label = str(node.get("label") or "").strip()
            if label and label in transcript and label not in hits:
                hits.append(label)
        return hits[:8]
    except Exception as e:
        logger.debug(f"课堂笔记知识点对齐跳过: {e}")
        return []


def create_note(user_id: str, subject_id: str, audio_data: bytes,
                filename: Optional[str] = None,
                model: Optional[str] = None) -> Dict[str, Any]:
    if not user_id or not subject_id:
        raise LectureError("缺少用户或学科", "invalid_request")
    if not audio_data:
        raise LectureError("音频为空", "invalid_request")

    from services.audio_service import AudioServiceError, transcribe_audio_detailed
    try:
        result = transcribe_audio_detailed(audio_data, filename=filename)
    except AudioServiceError as e:
        raise LectureError(e.message, e.error_type)
    transcript = str(result.get("text") or "").strip()
    if not transcript:
        raise LectureError("没有识别出文字，请确认录音内容", "asr_empty")

    summary = summarize_transcript(transcript, model)
    summary["knowledge_points"] = _align_knowledge_points(user_id, subject_id, transcript)
    note_id = "lec_" + uuid.uuid4().hex[:12]
    now = _now()
    title = summary.get("title") or (os.path.splitext(filename or "")[0] or "课堂笔记")
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO lecture_notes
               (id, user_id, subject_id, title, transcript, notes_json, source_name,
                asr_service, asr_engine, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (note_id, user_id, subject_id, str(title)[:80], transcript,
             json.dumps(summary, ensure_ascii=False), filename,
             result.get("service"), result.get("engine"), now),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"课堂笔记落库失败: {e}", exc_info=True)
        raise LectureError("课堂笔记保存失败", "database_error")
    finally:
        conn.close()
    return {"id": note_id, "title": title, "summary": summary,
            "transcript_chars": len(transcript), "asr_service": result.get("service"),
            "asr_engine": result.get("engine"), "created_at": now}


def list_notes(user_id: str, subject_id: Optional[str] = None,
               limit: int = 50) -> List[Dict[str, Any]]:
    conn = _conn()
    try:
        sql = ("SELECT id, subject_id, title, notes_json, source_name, asr_service, "
               "asr_engine, LENGTH(transcript) AS transcript_chars, created_at "
               "FROM lecture_notes WHERE user_id=?")
        args: List[Any] = [user_id]
        if subject_id:
            sql += " AND subject_id=?"
            args.append(subject_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit or 50), 200)))
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["summary"] = json.loads(item.pop("notes_json") or "{}")
        except ValueError:
            item["summary"] = {}
        out.append(item)
    return out


def get_note(user_id: str, note_id: str) -> Optional[Dict[str, Any]]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM lecture_notes WHERE id=? AND user_id=?",
            (note_id, user_id)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    item = dict(row)
    try:
        item["summary"] = json.loads(item.pop("notes_json") or "{}")
    except ValueError:
        item["summary"] = {}
    return item


__all__ = ["LectureError", "summarize_transcript", "create_note",
           "list_notes", "get_note"]
