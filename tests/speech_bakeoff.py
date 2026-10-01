"""
本地语音选型实测（TTS: CosyVoice vs GPT-SoVITS；ASR: FunASR）

用法（在语音栈 venv 下运行，模型服务需已启动）：
    # 1) 先启动本地语音服务（含 CosyVoice + FunASR）
    python -m speech_service.server
    # 2) 跑评测
    python tests/speech_bakeoff.py [--gptsovits] [--out DIR]

测什么（对应本轮要求"在同一批教学文本上实际比较"）：
- 中文 / 中英混读 / 数字与单位 / 长回答 四类教学文本
- 首段可播放等待时间、整段合成耗时、RTF（合成耗时 ÷ 音频时长）
- 长回答完整性（切分是否漏句：合成句数 vs 切分句数）
- 显存占用、冷启动时间、安装与重复启动稳定性（由脚本外的部署记录补充）
- **可懂度**：把合成音频再送回 ASR（本地 FunASR），与原文比对字符错误率（CER）
  —— 这是"机器可测"的朗读正确性证据；主观自然度仍需人工试听样本

产出：样本 WAV + 一份 JSON 报告（默认写到 --out 目录）
"""

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.request
import wave
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPEECH_URL = os.environ.get("LOCAL_SPEECH_URL", "http://127.0.0.1:8100")
# 主后端（生产路径：先做朗读文本归一化与按句切分，再调用本地语音服务）
BACKEND_URL = os.environ.get("EDU_BACKEND_URL", "http://127.0.0.1:8000")
_token_cache: Dict[str, str] = {}
DEFAULT_OUT = os.path.abspath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "speech_stack", "samples"))

# 同一批教学文本（覆盖本轮要求的四类）
TEXTS = {
    "中文课程问题": "二叉树的定义是什么？请用简单的语言解释一下。",
    "中英混读": "我们使用 GPU 和 RTX 4060 显卡，运行 DeepSeek 与 GPT-SoVITS 模型。",
    "数字与单位": "准确率提升了 3.14 倍，显存占用约 8GB，推理耗时 1.5 秒。",
    "长回答": (
        "二叉树是每个节点最多有两个子树的树结构。"
        "前序遍历的顺序是根节点、左子树、右子树。"
        "中序遍历的顺序是左子树、根节点、右子树。"
        "后序遍历的顺序是左子树、右子树、根节点。"
        "在二叉搜索树中，左子树的所有节点都小于根节点，右子树的所有节点都大于根节点。"
        "因此查找、插入和删除的平均时间复杂度都是 O(log n)。"
    ),
}


def _post(path: str, body: Optional[dict], timeout: float = 600) -> Dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(SPEECH_URL + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get(path: str, timeout: float = 30) -> Dict[str, Any]:
    with urllib.request.urlopen(SPEECH_URL + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def wav_duration(raw: bytes) -> float:
    with wave.open(io.BytesIO(raw), "rb") as w:
        return round(w.getnframes() / float(w.getframerate()), 3)


def asr_text(raw: bytes) -> Dict[str, Any]:
    """把音频送回本地 ASR，得到文本（用于可懂度评估）"""
    boundary = "----bo" + str(int(time.time() * 1000))
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"probe.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        + raw + f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(SPEECH_URL + "/asr", data=body, method="POST",
                                 headers={"Content-Type":
                                          f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def cer(ref: str, hyp: str) -> float:
    """
    字符错误率（仅用于"读得对不对"的客观比较）

    这里用最朴素的编辑距离实现（参考文本 = 去标点后的原文）。
    """
    import re
    clean = lambda s: re.sub(r"[\s，。！？；：、,.!?;:]", "", s or "")
    r, h = clean(ref), clean(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, cr in enumerate(r, 1):
        cur = [i]
        for j, ch in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cr != ch)))
        prev = cur
    return round(prev[-1] / len(r), 4)


def _backend_token() -> Optional[str]:
    """注册一个临时账号拿令牌（后端需运行在隔离数据目录）"""
    if _token_cache.get("token"):
        return _token_cache["token"]
    import random
    import string
    name = "bo_" + "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    try:
        req = urllib.request.Request(
            BACKEND_URL + "/api/auth/register",
            data=json.dumps({"username": name, "password": "test-pass-123"}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        _token_cache["token"] = body["data"]["token"]
        return _token_cache["token"]
    except Exception as e:
        print(f"  （无法注册后端账号，改用语音服务直连：{e}）")
        return None


def backend_tts(text: str, voice: str = "中文女") -> Dict[str, Any]:
    """通过**主后端**逐句合成（生产路径：归一化 → 切分 → 本地 TTS）"""
    token = _backend_token()
    if not token:
        raise RuntimeError("后端不可用")
    req = urllib.request.Request(
        BACKEND_URL + "/api/audio/tts",
        data=json.dumps({"text": text, "voice": voice}, ensure_ascii=False).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-User-Token": token})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode())["data"]


def measure_cosyvoice(out_dir: str) -> Dict[str, Any]:
    """
    测量 CosyVoice：**生产路径**（主后端 /api/audio/tts，含朗读文本归一化）

    同时用语音服务直连测一次"未归一化"的原文，作为归一化前后的对照证据。
    """
    report: Dict[str, Any] = {"engine": "cosyvoice-300m-sft", "texts": {}}
    for name, text in TEXTS.items():
        t0 = time.time()
        data = backend_tts(text)
        total = time.time() - t0
        items = data.get("items") or []
        durations = [wav_duration(base64.b64decode(it["audio_b64"])) for it in items]
        latencies = [it.get("latency_ms") or 0 for it in items]
        wavs = [base64.b64decode(it["audio_b64"]) for it in items]
        merged = _concat_wav(wavs)
        path = os.path.join(out_dir, f"cosyvoice_{name}.wav")
        open(path, "wb").write(merged)
        asr_out = asr_text(merged)

        # 对照：不归一化的原文直连语音服务（证明归一化层的作用）
        raw_asr, raw_cer = None, None
        try:
            raw_data = _post("/tts/segments",
                             {"segments": _split_local(text), "voice": "中文女"})
            raw_wavs = [base64.b64decode(it["audio_b64"])
                        for it in (raw_data.get("items") or [])]
            if raw_wavs:
                raw_merged = _concat_wav(raw_wavs)
                open(os.path.join(out_dir, f"cosyvoice_raw_{name}.wav"), "wb").write(raw_merged)
                raw_asr = asr_text(raw_merged).get("text")
                raw_cer = cer(text, raw_asr or "")
        except Exception as e:
            raw_asr = f"对照失败：{e}"

        report["texts"][name] = {
            "segments_total": data.get("segments_total"),
            "segments_synthesized": len(items),
            "all_segments_present": len(items) == data.get("segments_total"),
            "first_segment_latency_s": round((latencies[0] / 1000.0), 2) if latencies else None,
            "synthesis_seconds": round(total, 2),
            "audio_duration_s": round(sum(durations), 2),
            "rtf_total": round(total / max(sum(durations), 0.01), 2),
            "truncated": data.get("truncated"),
            "omitted_chars": data.get("omitted_chars"),
            "asr_text": asr_out.get("text"),
            # CER 以"实际朗读的文本"为参照（归一化后），衡量的是 TTS 可懂度；
            # 与原始书面文本的差异由数字读法造成，不是朗读错误
            "asr_cer": cer(data.get("speech_text") or text, asr_out.get("text") or ""),
            "asr_cer_vs_original": cer(text, asr_out.get("text") or ""),
            "speech_text": data.get("speech_text"),
            "asr_cer_raw_text": raw_cer,
            "asr_text_raw": raw_asr,
            "service": data.get("service"),
            "engine": data.get("engine"),
            "sample": path,
        }
    return report


def measure_gptsovits(out_dir: str) -> Dict[str, Any]:
    """
    GPT-SoVITS（v2）最小可运行验证

    - 参考音频：用 CosyVoice 生成的一段音频（机器合成，不涉及真人音色授权）
    - 若依赖/模型缺失，返回 {"error": ...} 并如实记录（这就是选型依据）
    """
    stack = os.path.abspath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "speech_stack"))
    repo = os.path.join(stack, "GPT-SoVITS")
    ffmpeg_dir = os.path.join(stack, "ffmpeg_bin")
    if os.path.isdir(ffmpeg_dir):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    sys.path.insert(0, repo)
    sys.path.insert(0, os.path.join(repo, "GPT_SoVITS"))
    # GPT-SoVITS 的配置里用的是相对路径（GPT_SoVITS/pretrained_models/...），
    # 必须在仓库目录下运行，否则会被当成 HuggingFace repo id 解析
    os.chdir(repo)

    report: Dict[str, Any] = {"engine": "gpt-sovits-v2", "texts": {}}
    t_load0 = time.time()
    try:
        import torch
        from GPT_SoVITS.inference_webui import (change_gpt_weights,
                                                change_sovits_weights, get_tts_wav)
    except Exception as e:
        report["error"] = f"依赖导入失败：{type(e).__name__}: {e}"
        return report

    gpt_ckpt = os.path.join(repo, "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained",
                            "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt")
    sovits_ckpt = os.path.join(repo, "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained",
                               "s2G2333k.pth")
    for f in (gpt_ckpt, sovits_ckpt):
        if not os.path.exists(f):
            report["error"] = f"缺少预训练权重：{f}"
            return report

    try:
        change_sovits_weights(sovits_ckpt, "v2")
        change_gpt_weights(gpt_ckpt)
    except Exception as e:
        report["error"] = f"权重加载失败：{type(e).__name__}: {e}"
        return report
    report["load_seconds"] = round(time.time() - t_load0, 1)
    try:
        report["vram_peak_mb"] = round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1)
    except Exception:
        pass

    # 参考音频（用 CosyVoice 生成，合法可分发）
    ref_path = os.path.join(out_dir, "reference_cosyvoice.wav")
    prompt_text = "二叉树是每个节点最多有两个子树的树结构。"
    if not os.path.exists(ref_path):
        data = _post("/tts/segments", {"segments": [prompt_text], "voice": "中文女"})
        raw = base64.b64decode(data["items"][0]["audio_b64"])
        open(ref_path, "wb").write(raw)
    report["reference_audio"] = ref_path

    for name, text in TEXTS.items():
        try:
            t0 = time.time()
            chunks = list(get_tts_wav(ref_path, prompt_text, "中文", text, "中文",
                                      how_to_cut="不切", top_k=15, top_p=1,
                                      temperature=1, speed=1, ref_free=False, if_freeze=False,
                                      inp_refs=None))
            total = time.time() - t0
            import numpy as np
            import soundfile as sf
            audio = chunks[-1][1]
            data = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            if data.ndim == 2:
                data = data.T
            path = os.path.join(out_dir, f"gptsovits_{name}.wav")
            sf.write(path, data, 32000)
            raw = open(path, "rb").read()
            dur = wav_duration(raw)
            asr_out = asr_text(raw)
            report["texts"][name] = {
                "total_synthesis_s": round(total, 2),
                "audio_duration_s": dur,
                "rtf_total": round(total / max(dur, 0.01), 2),
                "asr_text": asr_out.get("text"),
                "asr_cer": cer(text, asr_out.get("text") or ""),
                "sample": path,
            }
        except Exception as e:
            report["texts"][name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return report


def _split_local(text: str) -> List[str]:
    """用主项目的切分逻辑（与服务端一致）"""
    from services.tts_text import split_sentences
    return split_sentences(text)


def _concat_wav(wavs: List[bytes]) -> bytes:
    frames = bytearray()
    params = None
    for raw in wavs:
        with wave.open(io.BytesIO(raw), "rb") as w:
            if params is None:
                params = w.getparams()
            frames += w.readframes(w.getnframes())
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(frames))
    return out.getvalue()


def _guard() -> None:
    """
    运行前检查：本脚本会经后端做 RAG/TTS/ASR 调用并写出样本文件，
    后端必须跑在隔离数据目录上，避免污染正式库。
    """
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests._isolation_guard import require_backend_isolated, require_isolated
    # 样本写到 speech_stack（仓库外），但仍要求显式声明数据目录以便追溯
    require_isolated("tests/speech_bakeoff.py",
                     "语音选型实测（会调用后端并写出样本音频/报告）")
    require_backend_isolated(BACKEND_URL, "语音选型实测（经后端合成与识别）")


def main():
    _guard()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--gptsovits", action="store_true",
                        help="同时跑 GPT-SoVITS 对比（需要其依赖与权重）")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    health = _get("/health")
    print(f"语音服务状态：{json.dumps(health.get('engines'), ensure_ascii=False)[:200]}")
    print(f"显存：{health.get('vram')}")

    result: Dict[str, Any] = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device": health.get("config", {}).get("device"),
        "vram_after_load": health.get("vram"),
        "engines": {k: v.get("state") for k, v in (health.get("engines") or {}).items()},
    }

    print("\n===== CosyVoice-300M-SFT =====")
    cv = measure_cosyvoice(args.out)
    for name, r in cv["texts"].items():
        print(f"  {name}: 段数={r['segments_synthesized']}/{r['segments_total']} "
              f"（不漏句={r['all_segments_present']}）"
              f" 首段={r['first_segment_latency_s']}s 总={r['synthesis_seconds']}s "
              f"音频={r['audio_duration_s']}s RTF={r['rtf_total']} "
              f"ASR-CER={r['asr_cer']}（未归一化对照={r.get('asr_cer_raw_text')}）")
        print(f"      归一化后识别：「{str(r.get('asr_text'))[:64]}」")
        if r.get("asr_text_raw"):
            print(f"      未归一化识别：「{str(r.get('asr_text_raw'))[:64]}」")
    result["cosyvoice"] = cv

    if args.gptsovits:
        print("\n===== GPT-SoVITS v2 =====")
        gs = measure_gptsovits(args.out)
        if gs.get("error"):
            print(f"  ✗ {gs['error']}")
        else:
            print(f"  加载={gs.get('load_seconds')}s 显存峰值={gs.get('vram_peak_mb')}MB")
            for name, r in gs["texts"].items():
                if r.get("error"):
                    print(f"  {name}: ✗ {r['error']}")
                else:
                    print(f"  {name}: 总={r['total_synthesis_s']}s 音频={r['audio_duration_s']}s "
                          f"RTF={r['rtf_total']} ASR-CER={r['asr_cer']}")
                    print(f"      识别回来：「{str(r.get('asr_text'))[:60]}」")
        result["gptsovits"] = gs

    path = os.path.join(args.out, "bakeoff_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n报告：{path}\n样本目录：{args.out}")


if __name__ == "__main__":
    main()
