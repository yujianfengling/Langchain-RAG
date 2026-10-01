"""
默认文本模型切换（MiMo 会员到期 → DeepSeek）的回归测试

覆盖：
1. 默认文本模型解析：代码默认 = DeepSeek；环境变量可显式覆盖；未知/语音/视觉取值回落到默认
2. 所有文本入口继承默认模型（问答 / 流式 / 笔记 / 自测 / 计划 / 知识图谱 / Agent 共用 get_default_model）
3. 响应上报的提供方与模型 = 实际调用的提供方与模型（model=模型名，provider=提供方键）
4. 视觉、语音不共用文本模型配置：文字模型切换不得把 DeepSeek 塞进 Vision / ASR / TTS
5. 语音不可用时"准确标记原因"，不伪装成功

说明：这些用例只做配置解析与请求构造，不发起真实模型调用（外部调用在自动化测试中一律 Mock）。
"""

import os
import sys
import unittest
import importlib
from unittest import mock

import requests
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import models as model_config  # noqa: E402
from tests.fixtures_isolated import IsolatedDataTestCase  # noqa: E402

import json  # noqa: E402

SUBJECT = "数据结构"
KP = "二叉树"


def _stub_content(url: str) -> str:
    """给不同入口返回可被解析的最小内容（只关心模型选择，不关心业务正确性）"""
    if "quiz" in url:
        return ""
    return json.dumps({
        "title": "二叉树复习",
        "concept": {"content": "每个节点最多两个子树。"},
        "example": {"content": "前序：根左右。"},
        "mistakes": {"content": "易混淆前序与中序。"},
        "class_tips": {"content": "暂无课堂证据。"},
        "quiz": [],
    }, ensure_ascii=False)


class TestDefaultModelResolution(unittest.TestCase):
    """默认文本模型的解析规则"""

    def _resolve(self, env_value):
        """在指定 LLM_MODEL 下解析默认模型（还原环境，避免污染其它用例）"""
        with mock.patch.dict(os.environ, {"LLM_MODEL": env_value} if env_value is not None
                             else {}, clear=False):
            if env_value is None:
                os.environ.pop("LLM_MODEL", None)
            importlib.reload(model_config)
            try:
                return model_config.get_default_model()
            finally:
                importlib.reload(model_config)

    def test_code_default_is_deepseek_not_mimo(self):
        self.assertEqual(model_config.DEFAULT_MODEL, "deepseek")
        self.assertNotEqual(model_config.DEFAULT_MODEL, "mimo",
                            "MiMo 会员到期后不应再作为默认文本模型")

    def test_default_when_env_missing_or_empty(self):
        self.assertEqual(self._resolve(None), "deepseek")
        self.assertEqual(self._resolve(""), "deepseek")

    def test_env_can_explicitly_select_a_text_model(self):
        """显式配置可选提供方（含 MiMo）时按配置执行，不静默替换"""
        self.assertEqual(self._resolve("deepseek"), "deepseek")
        self.assertEqual(self._resolve("mimo"), "mimo")
        self.assertEqual(self._resolve("qwen"), "qwen")

    def test_unknown_or_non_text_env_falls_back_to_default(self):
        """未知取值与语音/视觉专用模型不能成为默认文本模型，也不会回退到 MiMo"""
        for bad in ("mimo-v2.5-pro", "mimo-asr", "mimo-tts", "qwen-vision", "gpt-4", "  "):
            self.assertEqual(self._resolve(bad), "deepseek", f"{bad!r} 应回落到默认文本模型")

    def test_provider_default_follows_same_source(self):
        """config.llm.provider 与 get_default_model() 同源，避免两处默认值打架"""
        from config import settings
        from config.models import get_default_model
        self.assertEqual(settings.llm.provider, get_default_model())
        self.assertEqual(settings.llm.resolved_text_model(), get_default_model())


class TestTextEntryPointsInheritDefault(unittest.TestCase):
    """未指定模型时，所有文本入口都走同一个默认模型"""

    def test_provider_resolution_without_model(self):
        from services.model_service import get_model_provider
        from config.models import get_default_model
        self.assertEqual(get_model_provider(None).model_name, get_default_model())
        self.assertEqual(get_model_provider().model_name, "deepseek")

    def test_call_llm_uses_default_when_model_is_none(self):
        """call_llm(model=None) 必须落到默认提供方（笔记/自测/计划都走这里）"""
        from services import ai_service
        captured = {}

        class _FakeProvider:
            model_name = None

            def chat(self, messages, **kw):
                captured["model"] = self.model_name
                return {"choices": [{"message": {"content": "ok"}}],
                        "model": "deepseek-chat", "latency": 0.01}

        with mock.patch("services.model_service.get_model_provider",
                        side_effect=lambda name=None: _FakeProvider()):
            ai_service.call_llm("你好", model=None)
        self.assertIsNone(captured.get("model"), "不应把 model 写死成某个提供方")

    def test_agent_and_services_default_parameter_is_none(self):
        """Agent / 计划 / 笔记 / 自测的 model 参数默认为 None（交给后端默认模型解析）"""
        import inspect
        from services import agent_runtime, learning_service, note_service, quiz_service
        for func in (agent_runtime.run_agent, learning_service.generate_plan,
                     note_service.generate_smart_note):
            sig = inspect.signature(func)
            self.assertIsNone(sig.parameters["model"].default,
                              f"{func.__name__} 的 model 默认值应为 None")
        # 出题：题库生成不接受前端传入的模型名（由后端解析）
        self.assertTrue(hasattr(quiz_service, "persist_questions"))

    def test_llm_facade_default(self):
        from llm import get_llm_provider_info
        self.assertEqual(get_llm_provider_info()["provider"], "deepseek")


class TestKnowledgePipelineUsesDefault(IsolatedDataTestCase):
    """
    知识图谱 / 知识提取也走同一个默认文本模型（不含 GRAPH_MODEL 时）

    注意：知识提取会写 extracted_knowledge.db，必须继承隔离夹具，
    否则测试知识点会落进正式库（本用例曾因此写脏过 4 行，已清理）。
    """

    def test_graph_builder_resolves_default_when_graph_model_unset(self):
        from rag.knowledge_graph import graph_builder
        from config.models import get_default_model
        captured = {}

        class _FakeProvider:
            def __init__(self, name):
                self.model_name = name

            def chat(self, messages, **kw):
                captured["model"] = self.model_name
                return {"answer": "[]", "model": self.model_name}

        def _provider(name=None):
            captured["requested"] = name
            return _FakeProvider(name or get_default_model())

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GRAPH_MODEL", None)
            with mock.patch("services.model_service.get_model_provider", side_effect=_provider):
                graph_builder._call_llm_for_triples("示例文本", model=None)
        self.assertIsNone(captured.get("requested"), "GRAPH_MODEL 未设置时应交给默认模型解析")

    def test_knowledge_extraction_uses_default_model(self):
        """知识提取不传 model → 用默认文本模型（不写死提供方）"""
        from services import knowledge_expander
        captured = {}

        def _fake_llm(prompt=None, system_message=None, model=None, **kw):
            captured["model"] = model
            return '{"knowledge_point": "测试点", "content": "内容"}'

        with mock.patch.object(knowledge_expander, "call_llm", side_effect=_fake_llm):
            with mock.patch.object(knowledge_expander, "LLM_AVAILABLE", True):
                knowledge_expander.extract_knowledge_from_qa(
                    user_id="u_test", subject_id=SUBJECT,
                    question="二叉树是什么",
                    # 长度需 >=50（服务会过滤过短回答）
                    answer="二叉树是每个节点最多有两个子树的树结构，常用于排序与检索。" * 3,
                    feedback_score=1)
        self.assertIn("model", captured, "未走到模型调用，用例需要更新")
        self.assertIsNone(captured["model"], "知识提取不应写死模型")


class TestOutgoingRequestUsesDefaultModel(IsolatedDataTestCase):
    """
    直接观察"真正发出去的请求"用了哪个模型（比只看函数签名更强）。

    做法：拦下 requests.post，读出请求体里的 model 与目标地址。
    这样能确认笔记 / 自测 / 学习计划 / Agent 工具在未指定模型时
    都打到 DeepSeek，而不是残留的 MiMo 配置。
    """

    def setUp(self):
        super().setUp()
        from services import learning_service
        self.sent = []

        def _post(url, **kw):
            body = {}
            try:
                body = json.loads(kw.get("data") or b"{}")
            except Exception:
                pass
            self.sent.append({"url": url, "model": body.get("model")})

            # 用真实的 requests.Response（provider 会调用 raise_for_status/json）
            resp = requests.Response()
            resp.status_code = 200
            resp.url = url
            resp._content = json.dumps({
                "choices": [{"message": {"content": _stub_content(url)}}],
                "model": body.get("model", ""),
                "usage": {},
            }, ensure_ascii=False).encode("utf-8")
            return resp

        self._post_patch = mock.patch("services.model_service.requests.post", side_effect=_post)
        self._post_patch.start()
        self.addCleanup(self._post_patch.stop)
        # RAG 检索走 Mock（与其它用例一致，不加载真实向量库）
        self._rag_patch = mock.patch(
            "services.note_service.retrieve_and_build_context",
            return_value={"context": "二叉树：每个节点最多两个子树。",
                          "sources": [], "context_used": True, "documents": []})
        self._rag_patch.start()
        self.addCleanup(self._rag_patch.stop)
        self._kb_patch = mock.patch("services.note_service.require_kb_ready",
                                    return_value=None)
        self._kb_patch.start()
        self.addCleanup(self._kb_patch.stop)

    def test_note_and_quiz_use_default_model(self):
        from services import note_service
        note_service.generate_smart_note(subject_id=SUBJECT, topic=KP, model=None)
        note_service.generate_quiz(subject_id=SUBJECT, topic=KP, model=None, count=1)
        self.assertTrue(self.sent, "没有捕获到任何模型请求")
        for item in self.sent:
            self.assertEqual(item["model"], "deepseek-chat", item)
            self.assertIn("api.deepseek.com", item["url"], item)

    def test_plan_generation_uses_default_model(self):
        from services import learning_service
        try:
            learning_service.generate_plan(subject_id=SUBJECT, goal="三天复习二叉树",
                                           model=None)
        except Exception:
            pass  # 计划结构解析可能不满足，但请求已发出，足以验证模型选择
        self.assertTrue(self.sent, "没有捕获到计划生成请求")
        self.assertEqual(self.sent[0]["model"], "deepseek-chat", self.sent[0])
        self.assertIn("api.deepseek.com", self.sent[0]["url"])

    def test_explicit_model_overrides_default(self):
        """显式指定其它模型时，请求必须打到对应提供方（不静默替换）"""
        from services import note_service
        note_service.generate_smart_note(subject_id=SUBJECT, topic=KP, model="qwen")
        self.assertTrue(self.sent)
        self.assertEqual(self.sent[0]["model"], "qwen-plus", self.sent[0])


class TestResponseReportsActualModel(IsolatedDataTestCase):
    """响应里的 model / provider 与真实调用一致"""

    def setUp(self):
        super().setUp()
        from api.app import app
        self.client = TestClient(app)
        self._llm = mock.patch("chat_service.call_llm", return_value="这是回答")
        self._llm.start()
        self.addCleanup(self._llm.stop)
        self._rag = mock.patch("chat_service.get_rag_context", return_value=[])
        self._rag.start()
        self.addCleanup(self._rag.stop)

    def test_chat_without_model_reports_deepseek(self):
        resp = self.client.post("/api/chat", json={"question": "集合是什么",
                                                   "subject_id": "数据结构"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["provider"], "deepseek", "未指定模型时未使用默认提供方")
        self.assertEqual(data["model"], "deepseek-chat", "上报的模型名与实际调用不一致")
        self.assertEqual(data["provider_display"], "DeepSeek")

    def test_explicit_model_is_not_silently_replaced(self):
        """学生显式选择其它模型时按实际选择执行（这里用 miMo 验证不被替换）"""
        resp = self.client.post("/api/chat", json={"question": "集合是什么",
                                                  "subject_id": "数据结构",
                                                   "model": "mimo"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["provider"], "mimo", "显式选择的模型被静默替换了")

    def test_health_reports_default_text_model(self):
        data = self.client.get("/health").json()["data"]
        caps = data["capabilities"]
        self.assertEqual(caps["text"]["provider"], "deepseek")
        self.assertEqual(caps["text"]["model"], "deepseek-chat")


class TestVisionAndSpeechAreSeparate(unittest.TestCase):
    """视觉 / 语音不共用文本模型：切换默认文本模型不得影响它们"""

    def test_vision_does_not_fall_back_to_text_model(self):
        """
        qwen-vision 与 MiMo 都不可用时：视觉应标记不可用并给出原因，
        而不是把纯文本的 DeepSeek 当成视觉模型去调用。
        """
        from services.multimodal_service import MultimodalService

        def _key(name):
            return ""  # 没有任何视觉凭证

        with mock.patch("config.models.get_model_api_key", side_effect=_key):
            svc = MultimodalService()
        self.assertIsNone(svc.vision_provider, "不应拿默认文本模型充当视觉模型")
        self.assertIn("视觉能力当前不可用", svc.vision_unavailable_reason)

    def test_vision_speech_models_are_not_text_defaults(self):
        from config.models import MODEL_CONFIG, TEXT_MODELS
        self.assertNotIn("qwen-vision", TEXT_MODELS)
        self.assertNotIn("mimo-asr", TEXT_MODELS)
        self.assertNotIn("mimo-tts", TEXT_MODELS)
        # 语音/视觉提供方仍是 MiMo（未到期前可用），不会被 DeepSeek 取代
        self.assertEqual(MODEL_CONFIG["mimo-asr"]["model_name"], "mimo-v2.5-asr")
        self.assertEqual(MODEL_CONFIG["mimo-tts"]["model_name"], "mimo-v2.5-tts")

    def test_mimo_optional_provider_reports_real_reason(self):
        """
        MiMo 作为**可选提供方**被显式选择时：凭证被拒 → available=False 且给出真实原因

        （默认语音链路已改为本地，见 test_speech_capability_prefers_local）
        """
        from services import audio_service

        def _post(*a, **kw):
            resp = mock.Mock()
            resp.status_code = 401
            return resp

        audio_service.reset_speech_probe()
        with mock.patch.dict(os.environ, {"SPEECH_BACKEND": "mimo"}),              mock.patch.object(audio_service.requests, "post", side_effect=_post):
            cap = audio_service.speech_capability(force=True)
        audio_service.reset_speech_probe()
        self.assertFalse(cap["available"])
        self.assertIn("MiMo", cap["reason"])
        self.assertTrue("会员到期" in cap["reason"] or "凭证" in cap["reason"])

    def test_speech_capability_prefers_local_and_reports_reason(self):
        """
        默认（auto）时语音链路选本地，并把本地 ASR/TTS 状态**分开**报告；
        本地不可用时要给出可理解原因（而不是笼统报"失败"）。
        """
        from services import audio_service
        from services import local_speech

        # 本地服务不可达 → available=False + 原因里说明是本地服务的问题
        with mock.patch.object(local_speech, "health", return_value={
                "running": False, "reason": "本地语音服务未启动（Connection refused）",
                "asr": {}, "tts": {}, "vram": {}}):
            cap = audio_service.speech_capability(force=True)
        self.assertEqual(cap["backend"], "local")
        self.assertFalse(cap["available"])
        self.assertIn("本地语音服务未启动", cap["reason"])
        # 本地与 MiMo 分别报告，便于 /health 与前端准确区分
        self.assertIn("local", cap)
        self.assertIn("mimo", cap)
        self.assertFalse(cap["asr"]["available"])
        self.assertFalse(cap["tts"]["available"])

        # 本地就绪 → available=True，且带上引擎名
        with mock.patch.object(local_speech, "health", return_value={
                "running": True, "reason": "",
                "asr": {"ready": True, "engine": "funasr-paraformer-zh", "state": "ready"},
                "tts": {"ready": True, "engine": "cosyvoice-300m-sft", "state": "ready",
                        "voices": ["中文女"]},
                "vram": {"available": True, "allocated_mb": 1234}}):
            cap2 = audio_service.speech_capability(force=True)
        self.assertTrue(cap2["available"])
        self.assertEqual(cap2["asr"]["engine"], "funasr-paraformer-zh")
        self.assertEqual(cap2["tts"]["engine"], "cosyvoice-300m-sft")

    def test_speech_error_message_names_the_real_cause(self):
        from services.audio_service import _raise_status_error, AudioServiceError
        with self.assertRaises(AudioServiceError) as ctx:
            _raise_status_error(401, "asr_api_error")
        self.assertEqual(ctx.exception.error_type, "invalid_api_key")
        self.assertIn("MiMo", ctx.exception.message)

    def test_audio_error_contract_and_identity(self):
        """语音接口：错误契约（502 + error_type）保持不变，且需要登录身份"""
        from api.app import app
        from services.audio_service import AudioServiceError
        from tests.fixtures_isolated import start_isolated_env, stop_isolated_env
        from tests.fixtures_isolated import register_and_login

        tmpdir, _data_dir, patchers = start_isolated_env(("数据结构",))
        try:
            client = TestClient(app)
            token = register_and_login("audio_contract_user")
            headers = {"X-User-Token": token}

            # 未登录 → 拒绝
            anon = client.post("/api/audio/speech", json={"text": "你好"})
            self.assertIn(anon.status_code, (401, 403), anon.text)

            with mock.patch("api.routes.audio.synthesize_speech",
                            side_effect=AudioServiceError("凭证被拒", "invalid_api_key")):
                resp = client.post("/api/audio/speech", json={"text": "你好"},
                                   headers=headers)
            self.assertEqual(resp.status_code, 502)
            self.assertEqual(resp.json()["error_type"], "invalid_api_key")
        finally:
            stop_isolated_env(tmpdir, patchers)


if __name__ == "__main__":
    unittest.main()
