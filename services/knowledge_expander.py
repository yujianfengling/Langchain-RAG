"""
知识库动态扩展模块（V2.6 Agent核心）

从用户交互中提取有价值的知识，验证后添加到知识库。

设计原则：
- 从高质量问答中提取知识点
- 使用LLM验证和改写知识
- 向量化后添加到FAISS索引
- 更新BM25索引
"""

import os
import json
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime
from utils.logger import logger

from services.user_profiler import (
    _get_db,
    _row_to_dict,
    get_user_interactions,
)

try:
    from services.ai_service import call_llm, AIServiceError
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

try:
    from rag import add_documents, split_text, get_embedding_model
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False

# 知识提取数据库
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
EXTRACTED_KB_PATH = os.path.join(_DATA_DIR, "extracted_knowledge.db")


def _get_extracted_db():
    """获取提取知识数据库连接"""
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = __import__('sqlite3').connect(EXTRACTED_KB_PATH)
    conn.row_factory = __import__('sqlite3').Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_extracted_db():
    """初始化提取知识表"""
    conn = _get_extracted_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS extracted_knowledge (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            knowledge_point TEXT NOT NULL,
            content TEXT NOT NULL,
            source_type TEXT DEFAULT 'qa_pair',
            source_interaction_id TEXT,
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            validated_at TEXT,
            added_to_kb_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_extracted_subject ON extracted_knowledge(subject_id, status);
        CREATE INDEX IF NOT EXISTS idx_extracted_confidence ON extracted_knowledge(confidence DESC);
        """)
        conn.commit()
    finally:
        conn.close()


_init_extracted_db()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def extract_knowledge_from_qa(
    user_id: str,
    subject_id: str,
    question: str,
    answer: str,
    feedback_score: int = 0,
) -> Optional[str]:
    """
    从问答对中提取知识

    只有高质量问答（正向反馈）才会被提取。

    Args:
        user_id: 用户ID
        subject_id: 学科ID
        question: 用户问题
        answer: AI回答
        feedback_score: 反馈分数 (1=好, -1=差, 0=未评价)

    Returns:
        提取的知识ID，如果没有提取则返回None
    """
    # 只提取正向反馈或未评价的高质量回答
    if feedback_score < 0:
        return None

    # 过滤太短的回答
    if len(answer) < 50:
        return None

    try:
        # 使用LLM提取知识点
        if not LLM_AVAILABLE:
            return None

        prompt = f"""请从以下问答对中提取一个有价值的知识点。

## 问题
{question}

## 回答
{answer}

## 要求
1. 提取一个独立、完整的知识点
2. 用简洁清晰的语言描述
3. 保留关键信息，去除冗余

请以JSON格式返回：
{{
  "knowledge_point": "知识点名称",
  "content": "知识点详细内容"
}}

如果问答没有值得提取的知识点，返回：
{{ "knowledge_point": null, "content": null }}"""

        result = call_llm(
            prompt=prompt,
            system_message="你是一名知识提取专家。请从问答对中提取有价值的知识点。",
            temperature=0.3,
            max_tokens=500,
        )

        # 解析结果
        try:
            json_start = result.find('{')
            json_end = result.rfind('}') + 1
            if json_start != -1 and json_end > json_start:
                parsed = json.loads(result[json_start:json_end])
                kp = parsed.get("knowledge_point")
                content = parsed.get("content")

                if kp and content:
                    # 保存到数据库
                    kid = str(uuid.uuid4())
                    conn = _get_extracted_db()
                    try:
                        now = _now()
                        conn.execute(
                            """INSERT INTO extracted_knowledge 
                               (id, subject_id, knowledge_point, content, source_type, 
                                source_interaction_id, confidence, status, created_at)
                               VALUES (?, ?, ?, ?, 'qa_pair', ?, 0.6, 'pending', ?)""",
                            (kid, subject_id, kp, content, None, now)
                        )
                        conn.commit()
                        logger.info(f"提取知识: {kp} (subject={subject_id})")
                        return kid
                    finally:
                        conn.close()
        except json.JSONDecodeError:
            logger.warning("知识提取结果解析失败")

        return None
    except Exception as e:
        logger.warning(f"知识提取失败（不影响主流程）: {e}")
        return None


def validate_knowledge(knowledge_id: str) -> Dict[str, Any]:
    """
    验证提取的知识

    使用LLM评估知识的准确性和价值。

    Returns:
        {
            "is_valid": bool,
            "confidence": float,
            "reason": str,
        }
    """
    try:
        conn = _get_extracted_db()
        try:
            cursor = conn.execute(
                "SELECT * FROM extracted_knowledge WHERE id = ?",
                (knowledge_id,)
            )
            row = cursor.fetchone()
            if not row:
                return {"is_valid": False, "confidence": 0, "reason": "知识不存在"}

            knowledge = _row_to_dict(row)

            if not LLM_AVAILABLE:
                # 没有LLM时，默认通过验证
                return {"is_valid": True, "confidence": 0.5, "reason": "LLM不可用，默认通过"}

            prompt = f"""请评估以下知识点的质量和准确性。

## 知识点
{knowledge['knowledge_point']}

## 内容
{knowledge['content']}

## 评估标准
1. 内容是否准确？
2. 描述是否清晰完整？
3. 是否有学习价值？

请以JSON格式返回：
{{
  "is_valid": true/false,
  "confidence": 0.0-1.0,
  "reason": "评估原因"
}}"""

            result = call_llm(
                prompt=prompt,
                system_message="你是一名知识质量评估专家。请客观评估知识点的质量。",
                temperature=0.2,
                max_tokens=300,
            )

            try:
                json_start = result.find('{')
                json_end = result.rfind('}') + 1
                if json_start != -1 and json_end > json_start:
                    parsed = json.loads(result[json_start:json_end])

                    # 更新验证状态
                    now = _now()
                    new_status = "validated" if parsed.get("is_valid") else "rejected"
                    new_confidence = parsed.get("confidence", 0.5)

                    conn.execute(
                        """UPDATE extracted_knowledge 
                           SET status = ?, confidence = ?, validated_at = ?
                           WHERE id = ?""",
                        (new_status, new_confidence, now, knowledge_id)
                    )
                    conn.commit()

                    return {
                        "is_valid": parsed.get("is_valid", False),
                        "confidence": new_confidence,
                        "reason": parsed.get("reason", ""),
                    }
            except json.JSONDecodeError:
                pass

            return {"is_valid": True, "confidence": 0.5, "reason": "解析失败，默认通过"}
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"知识验证失败: {e}", exc_info=True)
        return {"is_valid": False, "confidence": 0, "reason": str(e)}


def add_validated_knowledge_to_kb(
    knowledge_id: str,
    subject_id: str,
) -> bool:
    """
    将验证通过的知识添加到知识库

    1. 获取知识内容
    2. 文本分割
    3. 向量化
    4. 添加到FAISS索引

    Returns:
        是否添加成功
    """
    try:
        conn = _get_extracted_db()
        try:
            cursor = conn.execute(
                "SELECT * FROM extracted_knowledge WHERE id = ? AND status = 'validated'",
                (knowledge_id,)
            )
            row = cursor.fetchone()
            if not row:
                logger.warning(f"知识不存在或未验证: {knowledge_id}")
                return False

            knowledge = _row_to_dict(row)
        finally:
            conn.close()

        if not RAG_AVAILABLE:
            logger.warning("RAG模块不可用，无法添加到知识库")
            return False

        # 组合知识内容
        full_content = f"## {knowledge['knowledge_point']}\n\n{knowledge['content']}"

        # 添加到知识库
        try:
            # 使用现有的文档添加功能
            from rag.loaders.text_loader import load_text
            from rag.text_splitter import split_documents
            from rag.vector_store import add_documents_to_store

            # 创建文档对象
            from langchain.schema import Document
            doc = Document(
                page_content=full_content,
                metadata={
                    "source": "extracted_knowledge",
                    "knowledge_id": knowledge_id,
                    "knowledge_point": knowledge['knowledge_point'],
                    "subject_id": subject_id,
                    "extraction_date": knowledge['created_at'],
                }
            )

            # 分割文档
            chunks = split_documents([doc])

            # 添加到向量存储
            add_documents_to_store(chunks, subject_id)

            # 更新状态
            now = _now()
            conn = _get_extracted_db()
            try:
                conn.execute(
                    "UPDATE extracted_knowledge SET status = 'added', added_to_kb_at = ? WHERE id = ?",
                    (now, knowledge_id)
                )
                conn.commit()
            finally:
                conn.close()

            logger.info(f"知识已添加到知识库: {knowledge['knowledge_point']}")
            return True
        except Exception as e:
            logger.error(f"添加到知识库失败: {e}", exc_info=True)
            return False
    except Exception as e:
        logger.error(f"添加验证知识失败: {e}", exc_info=True)
        return False


def get_pending_knowledge(subject_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """获取待验证的知识"""
    conn = _get_extracted_db()
    try:
        if subject_id:
            cursor = conn.execute(
                """SELECT * FROM extracted_knowledge 
                   WHERE subject_id = ? AND status = 'pending'
                   ORDER BY confidence DESC, created_at DESC
                   LIMIT ?""",
                (subject_id, limit)
            )
        else:
            cursor = conn.execute(
                """SELECT * FROM extracted_knowledge 
                   WHERE status = 'pending'
                   ORDER BY confidence DESC, created_at DESC
                   LIMIT ?""",
                (limit,)
            )
        return [_row_to_dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"获取待验证知识失败: {e}", exc_info=True)
        return []
    finally:
        conn.close()


def get_knowledge_extraction_stats(subject_id: Optional[str] = None) -> Dict[str, Any]:
    """获取知识提取统计"""
    conn = _get_extracted_db()
    try:
        if subject_id:
            cursor = conn.execute(
                """SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'validated' THEN 1 ELSE 0 END) as validated,
                    SUM(CASE WHEN status = 'added' THEN 1 ELSE 0 END) as added,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
                   FROM extracted_knowledge WHERE subject_id = ?""",
                (subject_id,)
            )
        else:
            cursor = conn.execute(
                """SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'validated' THEN 1 ELSE 0 END) as validated,
                    SUM(CASE WHEN status = 'added' THEN 1 ELSE 0 END) as added,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
                   FROM extracted_knowledge"""
            )
        stats = _row_to_dict(cursor.fetchone())
        return {
            "total": stats.get("total", 0),
            "pending": stats.get("pending", 0),
            "validated": stats.get("validated", 0),
            "added": stats.get("added", 0),
            "rejected": stats.get("rejected", 0),
        }
    except Exception as e:
        logger.error(f"获取知识提取统计失败: {e}", exc_info=True)
        return {"total": 0, "pending": 0, "validated": 0, "added": 0, "rejected": 0}
    finally:
        conn.close()


__all__ = [
    "extract_knowledge_from_qa",
    "validate_knowledge",
    "add_validated_knowledge_to_kb",
    "get_pending_knowledge",
    "get_knowledge_extraction_stats",
]
