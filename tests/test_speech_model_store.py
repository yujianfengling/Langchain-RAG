"""轻量语音模型仓库的回归测试（2026-10-01；2026-10-01 起只有 ASR）

覆盖"按需下载"的关键契约，**不联网、不下载任何模型**：
1. 清单必须版本固定、带 sha256 与精确字节数（否则无法校验完整性/提示大小）
2. 未下载时状态为 absent；`ensure(allow_download=False)` 必须抛 model_missing
   并给出"需要多少 MB"（/health、/voices、warmup 依赖这条不下载）
3. 断网模式（SPEECH_MODEL_OFFLINE=1）给出可读的断网提示（model_download_failed）
4. 手动目录（SPEECH_ASR_MODEL_DIR）命中即用、零下载
5. 非 ASCII 路径要被识别出来（历史上会让第三方语音库直接崩）
6. 引擎注册与"引擎自己的模型"（防止轻量 ASR 被误判成未下载的回归）
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from speech_service import model_store  # noqa: E402


class ManifestContractTest(unittest.TestCase):
    """清单本身的硬约束"""

    def test_every_entry_is_pinned_and_verifiable(self):
        for key, spec in model_store.MANIFEST.items():
            with self.subTest(key=key):
                self.assertTrue(spec.url.startswith("https://"), spec.url)
                self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", spec.sha256),
                                f"{key} 的 sha256 不是 64 位十六进制")
                self.assertGreater(spec.size_bytes, 1024 * 1024, f"{key} 缺少精确字节数")
                self.assertIn(spec.version, spec.filename,
                              f"{key} 的版本没有体现在文件名里（版本固定）")
                self.assertTrue(spec.required_files, f"{key} 未声明必需文件")
                self.assertTrue(spec.license, f"{key} 未标注许可")

    def test_recommended_models_are_light(self):
        """推荐 ASR 模型必须远小于旧栈（≤1.5 GB 目标）；TTS 模型已整体移除"""
        total = model_store.MANIFEST[model_store.DEFAULT_ASR_MODEL].size_bytes
        self.assertLess(total, 1.5 * 1024 ** 3, "ASR 模型超过 1.5 GB 目标")
        self.assertLess(total, 400 * 1024 * 1024, "ASR 模型应明显小于 400 MB")


class StateAndGateTest(unittest.TestCase):
    """状态判定与"不下载"闸门"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="speech_models_")
        self.cache = Path(self._tmp.name) / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self._patches = [
            mock.patch.dict(os.environ, {
                "SPEECH_MODEL_CACHE_DIR": str(self.cache),
                "SPEECH_ASR_MODEL_DIR": "",
                "SPEECH_MODEL_DIR": "", "SPEECH_MODEL_OFFLINE": "",
            }),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_absent_then_ready(self):
        spec = model_store.get_spec("asr-sensevoice-int8")
        st = model_store.state(spec)
        self.assertFalse(st["ready"])
        self.assertEqual(st["status"], "absent")
        self.assertEqual(st["download_bytes"], spec.size_bytes)

        # 伪造一个"已就绪"的模型目录（内容无关，只验证判定逻辑）
        d = model_store.cache_root() / spec.key
        d.mkdir(parents=True, exist_ok=True)
        for rel in spec.required_files:
            (d / rel).write_bytes(b"x")
        st2 = model_store.state(spec)
        self.assertTrue(st2["ready"])
        self.assertEqual(st2["status"], "ready")
        self.assertGreater(st2["disk_bytes"], 0)

    def test_ensure_without_download_is_refused_with_size_hint(self):
        """/health、/voices、warmup 走这条：缺模型必须报错且**不联网**"""
        spec = model_store.get_spec("asr-sensevoice-int8")
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("不得发起网络请求")):
            with self.assertRaises(model_store.ModelStoreError) as ctx:
                model_store.ensure(spec, allow_download=False)
        self.assertEqual(ctx.exception.error_type, "model_missing")
        self.assertIn("尚未下载", ctx.exception.message)
        self.assertIn(f"{spec.size_mb:.1f} MB", ctx.exception.message)

    def test_offline_mode_reports_readable_reason(self):
        spec = model_store.get_spec("asr-sensevoice-int8")
        with mock.patch.dict(os.environ, {"SPEECH_MODEL_OFFLINE": "1"}):
            with self.assertRaises(model_store.ModelStoreError) as ctx:
                model_store.ensure(spec, allow_download=True)
        self.assertEqual(ctx.exception.error_type, "model_download_failed")
        self.assertIn("无法联网", ctx.exception.message)
        # 提示里要给出可替代做法（手动指定目录）
        self.assertIn("MODEL_DIR", ctx.exception.message)

    def test_manual_dir_short_circuits_download(self):
        spec = model_store.get_spec("asr-sensevoice-int8")
        manual = Path(self._tmp.name) / "already_have"
        manual.mkdir(parents=True, exist_ok=True)
        for rel in spec.required_files:
            (manual / rel).write_bytes(b"x")
        with mock.patch.dict(os.environ, {"SPEECH_ASR_MODEL_DIR": str(manual)}):
            st = model_store.state(spec)
            self.assertTrue(st["ready"])
            self.assertEqual(st["source"], "manual_dir")
            with mock.patch("urllib.request.urlopen",
                            side_effect=AssertionError("不得发起网络请求")):
                self.assertEqual(Path(model_store.ensure(spec, allow_download=True)), manual)

    def test_non_ascii_cache_path_is_flagged(self):
        """实测：非 ASCII 路径会让 Kokoro 的 espeak-ng 初始化直接终止进程"""
        self.assertIsNotNone(model_store.ascii_warning(Path(r"D:\中文目录\models")))
        self.assertIsNone(model_store.ascii_warning(Path(r"D:\speech_models")))

    def test_describe_all_reports_totals(self):
        info = model_store.describe_all()
        self.assertEqual(info["total_models"], len(model_store.MANIFEST))
        self.assertGreater(info["required_download_mb"], 0)
        self.assertEqual(info["ready_count"], 0)   # 空缓存



class EngineWiringTest(unittest.TestCase):
    """引擎注册与"用引擎自己的模型"（回归：piper-zh 曾被判成未下载）"""

    def test_light_engines_are_registered(self):
        from speech_service.engines import LIGHT_ENGINES, build_asr_engine
        self.assertIn("sensevoice", LIGHT_ENGINES)
        # TTS 引擎（kokoro/piper-zh/cosyvoice）已整体移除
        self.assertNotIn("kokoro", LIGHT_ENGINES)
        self.assertNotIn("piper-zh", LIGHT_ENGINES)
        asr = build_asr_engine("sensevoice", allow_download=False)
        self.assertNotIn("kokoro", LIGHT_ENGINES)
        self.assertNotIn("piper-zh", LIGHT_ENGINES)
        self.assertEqual(asr.kind, "asr")

        # 构造引擎不得触发加载（更不得下载）
        self.assertIsNone(asr._model)


    def test_tts_model_download_is_gone(self):
        """TTS 模型下载项必须已从清单移除（新环境不会下载 TTS 模型）"""
        self.assertNotIn("tts-kokoro-int8-multi-lang-v1_1", model_store.MANIFEST)
        self.assertNotIn("tts-piper-zh-chaowen-int8", model_store.MANIFEST)
        self.assertFalse(hasattr(model_store, "DEFAULT_TTS_MODEL"))
        self.assertEqual([m.kind for m in model_store.MANIFEST.values()], ["asr"])
        with self.assertRaises(model_store.ModelStoreError):
            model_store.pick("tts")

    def test_asr_contract_method_name(self):
        """服务端用 fn.__name__ == 'transcribe' 选锁，方法名不能改"""
        from speech_service.engines import build_asr_engine
        asr = build_asr_engine("sensevoice", allow_download=False)
        self.assertEqual(asr.transcribe.__name__, "transcribe")

    def test_default_asr_engine_still_constructs_with_full_kwargs(self):
        """回归：服务端统一传 model_key/num_threads/allow_download，
        默认的 FunASR 引擎必须仍能构造（不能因多余参数 TypeError）"""
        from speech_service import engines as eng
        from speech_service.engines import build_asr_engine
        extra = {"model_key": "x", "num_threads": 0, "allow_download": False,
                 "model": "paraformer-zh", "vad_model": "fsmn-vad", "punc_model": "ct-punc",
                 "model_dir": "nonexistent"}
        asr = build_asr_engine("funasr", **extra)      # 只构造，不加载
        self.assertIsNotNone(asr)
        self.assertIsNone(asr._model)
        # TTS 引擎构造入口已随功能移除
        self.assertFalse(hasattr(eng, "build_tts_engine"))


if __name__ == "__main__":
    unittest.main(verbosity=2)