"""试用前权限与 teacher_only 泄露修复的回归测试（2026-10-01）

对应第一轮修复的验收点：

1. `/api/chat`、`/api/chat/stream`、`/api/notes/generate` 复用**课程归属与在班权限**
   规则：课程学科仅授课教师 / 在班学生 / 管理员可读；匿名请求（会被回退成
   `default_user`）不得借该身份读取课程资料。
2. 历史（公共）学科保持既有语义："登录即可读"，不受课程归属约束。
3. `/education/*` 会调用付费模型，必须登录。
4. `teacher_only`（教师草稿）资料**不得**出现在学生问答的上下文与"参考来源"中；
   过滤以稳定的 `material_id` 为准（文件名仅作旧索引的兼容回退），
   并且"改可见范围 / 删除资料"无需重建索引即刻生效；授课教师与管理员不受影响。
5. BM25 索引、知识图谱、知识提取库、学科资料目录、日志目录、视觉缓存
   都随 `EDU_DATA_DIR` 隔离，不再写项目正式 `data/`。

隔离：全部用例走 `IsolatedDataTestCase`（临时数据目录 + 临时 SQLite），
外部模型一律 Mock，不调用真实 RAG/LLM。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

from tests.fixtures_isolated import IsolatedDataTestCase, register_and_login

# 历史（公共）学科：无课程归属
LEGACY_SUBJECT = "数据结构"
SECRET = "TEACHER_ONLY_SECRET_MARKER_9F3A"
PUBLIC_TEXT = "PUBLIC_HANDOUT_MARKER_4B71"

CHAT_STUB = {"success": True, "answer": "stub-answer", "sources": [],
             "model": "stub-model", "provider": "stub"}
NOTE_STUB = {"title": "stub-note", "context_used": True, "evidence_count": 1,
             "sections": [], "sources": []}
EDU_STUB = {"key_points": [], "summary": "stub-summary", "model": "stub"}


class GuardTestCase(IsolatedDataTestCase):
    """公共夹具：教师 / 管理员 / 在班学生 / 班外学生 + 课程学科 + 两份资料"""

    SUBJECTS = (LEGACY_SUBJECT,)
    TEACHER_USERS = "pg_teacher"
    ADMIN_USERS = "pg_admin"

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        from api.app import app

        self._env = mock.patch.dict(os.environ, {
            "TEACHER_USERS": self.TEACHER_USERS,
            "ADMIN_USERS": self.ADMIN_USERS,
        })
        self._env.start()
        self.addCleanup(self._env.stop)

        self.client = TestClient(app, raise_server_exceptions=False)

        self.t_teacher = register_and_login("pg_teacher")
        self.t_admin = register_and_login("pg_admin")
        self.t_student_in = register_and_login("pg_student_in")
        self.t_student_out = register_and_login("pg_student_out")

        # 教师建课（同时创建课程专属学科）
        resp = self.client.post("/api/teacher/courses", headers=self.h(self.t_teacher),
                                json={"title": "权限回归课程"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.course = resp.json()["data"]
        self.course_id = self.course["id"]
        self.subject_id = self.course["subject_id"]

        # 建班 + 在班学生入班
        resp = self.client.post(f"/api/teacher/courses/{self.course_id}/classes",
                                headers=self.h(self.t_teacher), json={"name": "权限回归班"})
        self.assertEqual(resp.status_code, 200, resp.text)
        klass = resp.json()["data"]
        self.class_id, self.join_code = klass["id"], klass["join_code"]
        resp = self.client.post("/api/student/courses/join", headers=self.h(self.t_student_in),
                                json={"code": self.join_code})
        self.assertEqual(resp.status_code, 200, resp.text)

        # 两份资料：教师草稿（teacher_only）+ 学生可见
        from services import teaching_data as td
        self.td = td
        self.to_filename = "teacher_only_draft.md"
        self.to_material = td.upsert_material(
            self.course_id, self.subject_id, self.to_filename, size_bytes=64,
            status="ready", visibility="teacher_only", uploaded_by="pg")
        self.visible_filename = "public_handout.md"
        self.visible_material = td.upsert_material(
            self.course_id, self.subject_id, self.visible_filename, size_bytes=64,
            status="ready", visibility="students", uploaded_by="pg")

    # ---------- 辅助 ----------

    def h(self, token):
        return {"X-User-Token": token}

    def chat(self, token=None, subject_id=None, body=None):
        """调用 /api/chat，并替换掉真实的问答编排（只验证权限与过滤链路）"""
        headers = self.h(token) if token else {}
        payload = body or {"question": "这门课的重点是什么",
                           "subject_id": subject_id or self.subject_id}
        with mock.patch("api.routes.chat.chat_with_ai", return_value=CHAT_STUB):
            return self.client.post("/api/chat", headers=headers, json=payload)

    def secret_doc(self, material_id=None, file_name=None, text=SECRET):
        metadata = {"file_name": file_name or self.to_filename, "page": 1, "type": "text"}
        if material_id:
            metadata["material_id"] = material_id
        return Document(page_content=text, metadata=metadata)

    def visible_doc(self):
        return Document(page_content=PUBLIC_TEXT,
                        metadata={"file_name": self.visible_filename,
                                  "material_id": self.visible_material["id"],
                                  "page": 1, "type": "text"})


# =====================================================================
# 一、问答入口的学科归属（/api/chat、/api/chat/stream）
# =====================================================================

class ChatSubjectScopeTests(GuardTestCase):

    def test_anonymous_cannot_chat_course_subject(self):
        """匿名（会被回退成 default_user）不得读取课程学科资料"""
        r = self.chat()
        self.assertEqual(r.status_code, 403, r.text)

    def test_non_member_student_cannot_chat_course_subject(self):
        """已登录但不在该课程 → 403"""
        r = self.chat(self.t_student_out)
        self.assertEqual(r.status_code, 403, r.text)

    def test_member_student_can_chat_course_subject(self):
        r = self.chat(self.t_student_in)
        self.assertEqual(r.status_code, 200, r.text)

    def test_course_teacher_can_chat_own_subject(self):
        r = self.chat(self.t_teacher)
        self.assertEqual(r.status_code, 200, r.text)

    def test_admin_can_chat_course_subject(self):
        r = self.chat(self.t_admin)
        self.assertEqual(r.status_code, 200, r.text)

    def test_chat_stream_endpoint_is_guarded_too(self):
        """流式入口必须与非流式同一规则（此前两者都没有校验）"""
        r = self.client.post("/api/chat/stream", json={
            "question": "重点是什么", "subject_id": self.subject_id})
        self.assertEqual(r.status_code, 403, r.text)

    def test_legacy_subject_keeps_login_readable_rule(self):
        """历史/公共学科：登录用户保持可读（不被课程归属误伤）"""
        r = self.chat(self.t_student_out, subject_id=LEGACY_SUBJECT)
        self.assertEqual(r.status_code, 200, r.text)

    def test_empty_subject_is_not_treated_as_course_access(self):
        """subject_id 为空（不读任何知识库）时不触发学科归属拦截。

        注意 ChatRequest.subject_id 是必填字段，因此"不带学科"在协议上表现为空串；
        该路径不读知识库，保持放行（避免把"普通闲聊"误判成越权）。
        """
        r = self.chat(self.t_student_in, body={"question": "你好", "subject_id": ""})
        self.assertEqual(r.status_code, 200, r.text)


# =====================================================================
# 二、/api/notes/generate 与 /api/education/*
# =====================================================================

class NotesAndEducationGuardTests(GuardTestCase):

    def test_anonymous_notes_on_course_subject_forbidden(self):
        with mock.patch("api.routes.notes.generate_smart_note", return_value=NOTE_STUB):
            r = self.client.post("/api/notes/generate",
                                 json={"subject_id": self.subject_id, "topic": "重点"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_non_member_notes_on_course_subject_forbidden(self):
        with mock.patch("api.routes.notes.generate_smart_note", return_value=NOTE_STUB):
            r = self.client.post("/api/notes/generate", headers=self.h(self.t_student_out),
                                 json={"subject_id": self.subject_id, "topic": "重点"})
        self.assertEqual(r.status_code, 403, r.text)

    def test_member_notes_on_course_subject_allowed(self):
        with mock.patch("api.routes.notes.generate_smart_note", return_value=NOTE_STUB):
            r = self.client.post("/api/notes/generate", headers=self.h(self.t_student_in),
                                 json={"subject_id": self.subject_id, "topic": "重点"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_notes_on_legacy_subject_keeps_existing_rule(self):
        with mock.patch("api.routes.notes.generate_smart_note", return_value=NOTE_STUB):
            r = self.client.post("/api/notes/generate",
                                 json={"subject_id": LEGACY_SUBJECT, "topic": "二叉树"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_education_endpoints_require_login(self):
        """三个付费模型入口在匿名时必须 401（此前完全无鉴权）"""
        for path, body in (("/api/education/summary", {"text": "任意文本"}),
                           ("/api/education/explain", {"question": "任意题目"}),
                           ("/api/education/advice", {"history": []})):
            with self.subTest(path=path):
                r = self.client.post(path, json=body)
                self.assertEqual(r.status_code, 401, f"{path}: {r.text}")

    def test_education_endpoints_work_after_login(self):
        with mock.patch("api.routes.education.generate_summary", return_value=EDU_STUB):
            r = self.client.post("/api/education/summary",
                                 headers=self.h(self.t_student_in), json={"text": "任意文本"})
        self.assertEqual(r.status_code, 200, r.text)


# =====================================================================
# 三、teacher_only 资料不得进入学生问答的上下文与引用
# =====================================================================

class TeacherOnlyRetrievalTests(GuardTestCase):

    def _ask(self, token):
        """真实走 chat_service 的检索→上下文→来源链路，只替换检索结果与 LLM"""
        def _echo(**kw):
            return "PROMPT>>>" + str(kw.get("prompt", ""))

        with mock.patch("services.ai_service.retrieve_context",
                        return_value=[self.secret_doc(material_id=self.to_material["id"]),
                                      self.visible_doc()]), \
             mock.patch("chat_service.call_llm", side_effect=_echo):
            return self.client.post("/api/chat", headers=self.h(token),
                                    json={"question": "这门课的重点是什么",
                                          "subject_id": self.subject_id})

    def test_teacher_only_not_in_student_context(self):
        r = self._ask(self.t_student_in)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(SECRET, r.json().get("answer", ""),
                         "教师草稿内容不得进入学生问答的上下文")

    def test_teacher_only_not_in_student_sources(self):
        r = self._ask(self.t_student_in)
        names = [s.get("file_name") for s in (r.json().get("sources") or [])]
        self.assertNotIn(self.to_filename, names, "教师草稿不得出现在参考来源里")

    def test_visible_material_still_reaches_student(self):
        """过滤不能误伤"学生可见"的资料"""
        r = self._ask(self.t_student_in)
        self.assertIn(PUBLIC_TEXT, r.json().get("answer", ""),
                      "学生可见资料必须照常进入上下文")
        names = [s.get("file_name") for s in (r.json().get("sources") or [])]
        self.assertIn(self.visible_filename, names)

    def test_teacher_still_gets_own_draft(self):
        """教师本人必须能正常使用自己的草稿资料"""
        r = self._ask(self.t_teacher)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(SECRET, r.json().get("answer", ""))

    def test_admin_also_sees_draft(self):
        r = self._ask(self.t_admin)
        self.assertIn(SECRET, r.json().get("answer", ""))

    def test_visibility_change_takes_effect_without_reindex(self):
        """把草稿改为学生可见 → 无需重建索引，学生立刻能拿到"""
        self.td.set_material_visibility(self.to_material["id"], "students")
        r = self._ask(self.t_student_in)
        self.assertIn(SECRET, r.json().get("answer", ""))

    def test_deleted_material_chunks_are_dropped_before_reindex(self):
        """资料已删除但索引还没重建时，带 material_id 的分块也必须被丢弃"""
        self.td.delete_material(self.to_material["id"])
        r = self._ask(self.t_student_in)
        self.assertNotIn(SECRET, r.json().get("answer", ""))

    def test_filename_fallback_for_legacy_index(self):
        """旧索引没有 material_id：按文件名认领到草稿资料 → 同样过滤"""
        doc = self.secret_doc(material_id=None)
        with mock.patch("services.ai_service.retrieve_context", return_value=[doc]), \
             mock.patch("chat_service.call_llm", side_effect=lambda **kw: "P>>>" + kw.get("prompt", "")):
            r = self.client.post("/api/chat", headers=self.h(self.t_student_in),
                                 json={"question": "重点", "subject_id": self.subject_id})
        self.assertNotIn(SECRET, r.json().get("answer", ""))

    def test_unknown_filename_in_course_subject_is_kept(self):
        """未登记为课程资料的历史内容不被误伤（保持既有行为）"""
        doc = Document(page_content="LEGACY_DOC_MARKER",
                       metadata={"file_name": "legacy_notes.md", "page": 1})
        with mock.patch("services.ai_service.retrieve_context", return_value=[doc]), \
             mock.patch("chat_service.call_llm", side_effect=lambda **kw: "P>>>" + kw.get("prompt", "")):
            r = self.client.post("/api/chat", headers=self.h(self.t_student_in),
                                 json={"question": "重点", "subject_id": self.subject_id})
        self.assertIn("LEGACY_DOC_MARKER", r.json().get("answer", ""))

    def test_unknown_viewer_is_treated_as_student(self):
        """查看者身份缺失时 fail-closed（不能因为漏传身份就放行草稿）"""
        from services import teaching_access
        kept = teaching_access.filter_visible_documents(
            None, self.subject_id, [self.secret_doc(material_id=self.to_material["id"])])
        self.assertEqual(kept, [])

    def test_legacy_subject_documents_are_untouched(self):
        """历史/公共学科没有可见范围概念：原样返回"""
        from services import teaching_access
        docs = [self.secret_doc(material_id=self.to_material["id"])]
        self.assertEqual(
            teaching_access.filter_visible_documents(None, LEGACY_SUBJECT, docs), docs)


# =====================================================================
# 四、分块资料标识的写入（稳定标识的来源）
# =====================================================================

class MaterialIdTaggingTests(GuardTestCase):

    def test_chunks_get_stable_material_id(self):
        from services import knowledge_service
        doc = Document(page_content="内容",
                       metadata={"file_name": self.to_filename})
        other = Document(page_content="内容2",
                         metadata={"file_name": self.visible_filename})
        unknown = Document(page_content="内容3", metadata={"file_name": "unknown.md"})
        tagged = knowledge_service._attach_material_ids(
            self.subject_id, [doc, other, unknown])
        self.assertEqual(tagged, 2)
        self.assertEqual(doc.metadata["material_id"], self.to_material["id"])
        self.assertEqual(other.metadata["material_id"], self.visible_material["id"])
        self.assertNotIn("material_id", unknown.metadata)

    def test_legacy_subject_chunks_are_not_tagged(self):
        from services import knowledge_service
        doc = Document(page_content="内容", metadata={"file_name": "any.md"})
        self.assertEqual(knowledge_service._attach_material_ids(LEGACY_SUBJECT, [doc]), 0)
        self.assertNotIn("material_id", doc.metadata)


# =====================================================================
# 五、EDU_DATA_DIR 隔离覆盖面
# =====================================================================

class IsolationPathTests(IsolatedDataTestCase):
    """这些路径此前硬编码到项目正式 data/，隔离运行时仍会写正式目录"""

    SUBJECTS = (LEGACY_SUBJECT,)

    def _env_dir(self) -> Path:
        return Path(os.environ["EDU_DATA_DIR"]).resolve()

    def _assert_inside(self, path, label):
        p = Path(path).resolve()
        env_dir = self._env_dir()
        self.assertTrue(
            p == env_dir or env_dir in p.parents,
            f"{label} 未随 EDU_DATA_DIR 隔离：{p}（应位于 {env_dir} 下）")

    def test_runtime_resolved_paths_follow_data_dir(self):
        from rag import hybrid_retriever
        from rag.knowledge_graph import graph_builder
        from services import knowledge_expander

        self._assert_inside(hybrid_retriever._bm25_index_dir(), "BM25 索引目录")
        self._assert_inside(graph_builder._graph_dir(), "知识图谱目录")
        self._assert_inside(os.path.dirname(knowledge_expander._extracted_kb_path()),
                            "知识提取库目录")

    def test_answer_tts_is_gone(self):
        """回答朗读（TTS）已整体移除：不得再有 TTS 模型项与 TTS 缓存路径"""
        from services import audio_service
        from speech_service import model_store
        self.assertFalse(hasattr(audio_service, "_tts_cache_dir"),
                         "TTS 缓存路径不应再存在")
        self.assertEqual([m.kind for m in model_store.MANIFEST.values()], ["asr"])

    def test_vision_cache_paths_follow_data_dir(self):
        """验收时发现：视觉/图片描述缓存此前是相对路径，会把缓存写进正式 data/"""
        from rag.enrichment import multimodal_enricher
        from rag.multimodal import document_converter
        from rag.multimodal.pdf.processors import vision_processor

        self._assert_inside(multimodal_enricher.image_description_cache_dir(),
                            "图片描述缓存目录（enricher）")
        self._assert_inside(document_converter.image_description_cache_dir(),
                            "图片描述缓存目录（converter）")
        self._assert_inside(vision_processor.VisionProcessor.cache_dir(),
                            "视觉描述缓存目录（processor）")

    def test_config_paths_do_not_point_into_repo(self):
        """学科资料目录与日志目录不得再硬编码到 <仓库>/data、<仓库>/logs"""
        from config import settings

        repo_root = Path(__file__).resolve().parents[1]
        for label, value in (("subjects_data_path", settings.path.subjects_data_path),
                             ("log_dir", settings.path.log_dir)):
            p = Path(value).resolve()
            self.assertNotIn(repo_root, p.parents,
                             f"{label} 仍指向仓库内：{p}")

    def test_runtime_log_dir_follows_data_dir(self):
        from utils import logger as app_logger
        self._assert_inside(app_logger._default_log_dir(), "运行时日志目录")


    def test_exported_api_can_be_imported(self):
        """导出面没有被改坏（避免只改常量导致 import 失败）"""
        from rag.hybrid_retriever import retrieve as hybrid_retrieve  # noqa: F401
        from rag.knowledge_graph.graph_builder import (  # noqa: F401
            build_knowledge_graph, load_knowledge_graph, list_graphs)
        from services.knowledge_service import rebuild_subject_knowledge  # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)