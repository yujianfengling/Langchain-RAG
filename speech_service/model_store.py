"""轻量语音模型的按需下载与本地缓存（2026-10-01）

目标：**新机器不需要手工复制旧机的语音模型**。首次真正使用录音（语音输入）时才下载，
之后复用缓存并可离线运行。（回答朗读/TTS 已整体移除，这里只有 ASR 模型。）

硬性约束（与需求一一对应）：
1. 只在**真正使用**时下载：`/asr` 或显式下载接口。
   后端启动、打开网页、`GET /health`、`GET /voices` 一律**不下载**。
2. 缓存目录在**仓库之外**且可配置：`SPEECH_MODEL_CACHE_DIR`
   （默认 `<仓库父目录>/speech_models`）。
3. 可**手动指定已有模型目录**：`SPEECH_ASR_MODEL_DIR`
   （目录存在且必需文件齐全 → 直接使用，零下载）。
4. 下载前给出**明确的大小提示**；过程中报告进度（字节/百分比/速度/剩余时间）；
   失败按指数退避**重试**；完成后校验 **sha256** 与必需文件；**版本固定**在 MAINFEST；
   断网时给出可读提示（`model_download_failed` + 明确文案）。
5. 下载未完成或加载失败**不影响文字问答**；**不得静默改用 MiMo**（由上层保证）。

设计说明：
- 先下 `.part` 临时文件，校验通过后再原子改名 → 中断不会留下"看起来完整"的包。
- 解压到 `<模型目录>.part/` 再原子改名 → 解压失败不会留下半个模型目录。
- 完成后写 `.installed.json`（版本 + sha256 + 字节数），用于重启复用与离线判定。
- 本模块**不 import torch / funasr / cosyvoice**，只依赖标准库。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# ==================== 模型清单（版本固定） ====================
#
# 尺寸/哈希均来自官方 GitHub Release 资产元数据（k2-fsa/sherpa-onnx），
# 运行时本体是 sherpa-onnx（Apache-2.0），**不需要 PyTorch**。

_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass(frozen=True)
class ModelSpec:
    key: str                      # 稳定标识（缓存目录名）
    kind: str                     # asr | tts
    title: str                    # 给用户看的中文名
    version: str                  # 固定版本（资产日期）
    filename: str                 # 下载的压缩包名
    url: str
    sha256: str
    size_bytes: int               # 压缩包精确字节数（下载显示用）
    license: str                  # 许可（用于报告与展示）
    required_files: Tuple[str, ...] = ()   # 相对于模型目录的必需文件
    note: str = ""
    homepage: str = ""

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1024 / 1024


# 说明：
# - SenseVoice int8：中/英/日/韩/粤 五语、自带标点与 ITN（use_itn），无需额外标点模型。
# - Kokoro int8 多语 v1.1：含中文音色，体积小；英文术语走 espeak 音素化。
# - Piper zh_CN chaowen int8：极小（14 MB），作为"极限省盘"备选。
MANIFEST: Dict[str, ModelSpec] = {
    "asr-sensevoice-int8": ModelSpec(
        key="asr-sensevoice-int8",
        kind="asr",
        title="SenseVoiceSmall int8（中英日韩粤，自带标点/ITN）",
        version="2024-07-17",
        filename="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        url=f"{_RELEASE}/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        sha256="7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
        size_bytes=163002883,
        license="运行时 Apache-2.0；权重见 FunASR Model License v1.1（模型卡）",
        required_files=("model.int8.onnx", "tokens.txt"),
        note="替代 FunASR paraformer-zh + fsmn-vad + ct-punc（三者合计约 1.19 GB）",
        homepage="https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html",
    ),
}

DEFAULT_ASR_MODEL = "asr-sensevoice-int8"

MARKER_FILE = ".installed.json"


class ModelStoreError(Exception):
    """模型仓库异常（error_type 落在主后端已登记的枚举内）"""

    def __init__(self, message: str, error_type: str = "model_missing"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


# ==================== 目录解析 ====================

def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cache_root() -> Path:
    """模型缓存根目录（仓库之外，可配置，**默认找 ASCII 路径**）

    `SPEECH_MODEL_CACHE_DIR` > 默认 `<用户主目录>/speech_models`

    为什么默认不用"仓库父目录"：2026-10-01 实测发现，**模型路径含非 ASCII 字符**
    （例如仓库位于 `D:\\中文目录\\...`，其中的中文字符无法用 GBK/CP936 表示）会让
    sherpa-onnx 的 espeak-ng 初始化抛
    `Error processing file '...phontab': Illegal byte sequence` 并**直接终止进程**，
    表现为 Kokoro 多语 TTS 完全无法加载。用户主目录通常是纯 ASCII，可避开该问题。
    """
    raw = (os.getenv("SPEECH_MODEL_CACHE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "speech_models"


def is_ascii_path(path: Path) -> bool:
    """路径是否为纯 ASCII（espeak-ng 依赖的 C 函数按系统代码页转换路径）"""
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def ascii_warning(path: Path) -> Optional[str]:
    """非 ASCII 路径的提醒（会直接影响 Kokoro 多语 TTS）"""
    if is_ascii_path(path):
        return None
    return (f"模型目录含非 ASCII 字符（{path}）：在多语 TTS（Kokoro）上会因 "
            f"espeak-ng 路径编码失败而无法加载（实测报 Illegal byte sequence）。"
            f"请把 SPEECH_MODEL_CACHE_DIR 指向纯英文路径，例如 D:\\\\speech_models。")


def manual_dir(spec: ModelSpec) -> Optional[Path]:
    """手动指定的已有模型目录（命中则完全不下载）

    `SPEECH_ASR_MODEL_DIR`；也支持 `SPEECH_MODEL_DIR` 作为共同前缀（其下按 key 找目录）。
    """
    candidates: List[str] = []
    if spec.kind == "asr":
        candidates.append(os.getenv("SPEECH_ASR_MODEL_DIR") or "")
    shared = (os.getenv("SPEECH_MODEL_DIR") or "").strip()
    if shared:
        candidates.append(str(Path(shared) / spec.key))
        candidates.append(shared)
    for raw in candidates:
        raw = (raw or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.is_dir() and _has_required(p, spec):
            return p
    return None


def model_dir(spec: ModelSpec) -> Path:
    """该模型的最终目录（手动目录优先，否则缓存目录）"""
    manual = manual_dir(spec)
    return manual if manual else cache_root() / spec.key


def staged_dir(spec: ModelSpec) -> Path:
    return cache_root() / (spec.key + ".part")


def archive_path(spec: ModelSpec) -> Path:
    return cache_root() / "downloads" / spec.filename


def _has_required(path: Path, spec: ModelSpec) -> bool:
    return all((path / rel).exists() for rel in spec.required_files)


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


# ==================== 状态查询（绝不触发下载） ====================

def state(spec: ModelSpec) -> Dict:
    """该模型的本地状态；**只读磁盘，不联网、不下载**"""
    manual = manual_dir(spec)
    target = manual or (cache_root() / spec.key)
    ready = target.is_dir() and _has_required(target, spec)
    info: Dict = {
        "key": spec.key,
        "kind": spec.kind,
        "title": spec.title,
        "version": spec.version,
        "license": spec.license,
        "note": spec.note,
        "homepage": spec.homepage,
        "download_bytes": spec.size_bytes,
        "download_mb": round(spec.size_mb, 1),
        "sha256": spec.sha256,
        "dir": str(target),
        "source": "manual_dir" if manual else "cache",
        "required_files": list(spec.required_files),
        "ready": bool(ready),
        "installed": False,
        "disk_bytes": dir_size(target) if target.exists() else 0,
        "status": "ready" if ready else "absent",
    }
    marker = target / MARKER_FILE
    if marker.is_file():
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
            info["installed"] = True
            info["installed_version"] = meta.get("version")
            info["installed_sha256"] = meta.get("sha256")
            info["installed_at"] = meta.get("installed_at")
            if meta.get("version") != spec.version:
                info["status"] = "version_mismatch"
                info["ready"] = False
        except Exception:
            info["status"] = "marker_unreadable" if info["ready"] else info["status"]
    if not ready and info["status"] == "absent":
        partial = staged_dir(spec)
        if partial.exists() or archive_path(spec).with_suffix(archive_path(spec).suffix + ".part").exists():
            info["status"] = "partial"
    info["disk_mb"] = round(info["disk_bytes"] / 1024 / 1024, 1)
    return info


def describe_all() -> Dict:
    """所有模型的体积/状态总览（供 /health 与 /models/status）"""
    items = [state(s) for s in MANIFEST.values()]
    downloaded = sum(i["download_bytes"] for i in items if i["ready"])
    disk = sum(i["disk_bytes"] for i in items if i["ready"])
    return {
        "cache_dir": str(cache_root()),
        "cache_dir_configured": bool((os.getenv("SPEECH_MODEL_CACHE_DIR") or "").strip()),
        "models": items,
        "ready_count": sum(1 for i in items if i["ready"]),
        "total_models": len(items),
        "downloaded_mb": round(downloaded / 1024 / 1024, 1),
        "disk_mb": round(disk / 1024 / 1024, 1),
        "recommended": {
            "asr": DEFAULT_ASR_MODEL,
        },
        "required_download_mb": round(
            MANIFEST[DEFAULT_ASR_MODEL].size_bytes / 1024 / 1024, 1),
    }


# ==================== 下载 ====================

def _sha256_file(path: Path, progress: Optional[Callable] = None,
                 base: int = 0, total: int = 0) -> str:
    h = hashlib.sha256()
    done = base
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total, "verify")
    return h.hexdigest()


def _download_once(spec: ModelSpec, dest: Path, progress: Optional[Callable],
                   timeout: int = 30) -> int:
    """单次下载（写入 dest）；返回已下载字节数"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(spec.url, headers={
        "User-Agent": "smart-education-rag/speech-model-store",
        "Accept": "application/octet-stream",
    })
    started = time.time()
    done = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or spec.size_bytes)
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                elapsed = max(time.time() - started, 1e-6)
                progress(done, total, "download", speed=done / elapsed)
    return done


def download(spec: ModelSpec, progress: Optional[Callable] = None,
             retries: int = 3, force: bool = False) -> Path:
    """下载 + 校验 + 解压（幂等：已就绪直接返回）

    Args:
        progress: `fn(done, total, phase, speed=...)`；phase ∈ download/verify/extract
        retries: 网络失败重试次数（指数退避 2s/4s/8s）
        force: 忽略已安装状态，重新下载

    Raises:
        ModelStoreError: error_type ∈ {model_download_failed, model_missing}
    """
    cur = state(spec)
    if cur["ready"] and not force:
        return Path(cur["dir"])

    root = cache_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ModelStoreError(f"无法创建模型缓存目录 {root}：{e}", "model_download_failed")

    archive = archive_path(spec)
    part = archive.with_suffix(archive.suffix + ".part")

    # 断网/不可达的提前判定：给出可读提示，而不是等超时
    if not _network_hint_ok():
        raise ModelStoreError(
            f"当前无法联网，无法下载轻量语音模型「{spec.title}」"
            f"（需要 {spec.size_mb:.1f} MB）。可先联网重试，或用 "
            f"SPEECH_ASR_MODEL_DIR 指向已有模型目录。",
            "model_download_failed")

    last_err: Optional[Exception] = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            if part.exists():
                part.unlink()
            _download_once(spec, part, progress)
            got = part.stat().st_size
            if spec.size_bytes and got != spec.size_bytes:
                raise ModelStoreError(
                    f"下载不完整：期望 {spec.size_bytes} 字节，实际 {got} 字节",
                    "model_download_failed")
            digest = _sha256_file(part, progress, base=0, total=spec.size_bytes)
            if digest.lower() != spec.sha256.lower():
                raise ModelStoreError(
                    "下载文件校验失败（sha256 不一致），已丢弃，请重试或改用 "
                    "SPEECH_*_MODEL_DIR 指向已有模型目录", "model_download_failed")
            part.replace(archive)
            last_err = None
            break
        except ModelStoreError as e:
            last_err = e
            if part.exists():
                part.unlink()
            if e.error_type != "model_download_failed":
                raise
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
            last_err = ModelStoreError(
                f"下载失败（第 {attempt}/{retries} 次）：{e}", "model_download_failed")
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
        if attempt < retries:
            time.sleep(2 ** attempt)   # 2s / 4s / 8s

    if last_err is not None:
        raise last_err

    # 解压（先到 .part 目录，成功后原子改名）
    staged_root = staged_dir(spec)
    if staged_root.exists():
        shutil.rmtree(staged_root, ignore_errors=True)
    staged_root.mkdir(parents=True, exist_ok=True)
    staged = staged_root
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            _safe_extract(tar, staged, progress)
    except Exception as e:
        shutil.rmtree(staged_root, ignore_errors=True)
        raise ModelStoreError(f"解压失败：{e}", "model_download_failed")

    # tar 包内通常有一层同名目录，必要时下钻
    inner = _inner_dir(staged)
    if inner is not None:
        inner.replace(staged_root / "__inner__")
        staged = staged_root / "__inner__"

    if not _has_required(staged, spec):
        missing = [f for f in spec.required_files if not (staged / f).exists()]
        shutil.rmtree(staged_root, ignore_errors=True)
        raise ModelStoreError(f"解压后缺少必需文件：{missing}", "model_missing")

    final = cache_root() / spec.key
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    staged.replace(final)
    # 清掉解压根目录（下钻后可能残留空壳，避免留下 .part 干扰状态判断）
    shutil.rmtree(staged_root, ignore_errors=True)

    (final / MARKER_FILE).write_text(json.dumps({
        "key": spec.key, "version": spec.version, "sha256": spec.sha256,
        "size_bytes": spec.size_bytes, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": str(spec.url), "license": spec.license,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    # 下载包保留在 downloads/ 便于离线重装；可用 clear_downloads() 清理
    return final


def _inner_dir(root: Path) -> Optional[Path]:
    """tar 包解压后若只有一层目录，返回该目录（保持文件名可预期）"""
    entries = [p for p in root.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return None


def _safe_extract(tar: tarfile.TarFile, dest: Path, progress: Optional[Callable]) -> None:
    """防目录穿越的解压（并对进度做粗粒度上报）"""
    members = tar.getmembers()
    total = len(members) or 1
    base = str(dest.resolve())
    for i, m in enumerate(members, 1):
        target = str((dest / m.name).resolve())
        if not target.startswith(base):
            raise ModelStoreError(f"压缩包内路径越界：{m.name}", "model_download_failed")
        tar.extract(m, dest)
        if progress and (i % 20 == 0 or i == total):
            progress(i, total, "extract")


def _network_hint_ok() -> bool:
    """离线快速判定：`SPEECH_MODEL_OFFLINE=1` 视为离线；否则尝试一次 DNS 连接

    这里只做"能不能连上发布站点"的轻量探测（3 秒超时），失败即给出断网提示，
    避免让用户等一次漫长的下载超时。
    """
    if (os.getenv("SPEECH_MODEL_OFFLINE") or "").strip() in ("1", "true", "yes", "on"):
        return False
    import socket
    try:
        socket.setdefaulttimeout(3)
        socket.create_connection(("github.com", 443), timeout=3).close()
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


def ensure(spec: ModelSpec, progress: Optional[Callable] = None,
           allow_download: bool = True) -> Path:
    """取得可用模型目录（**首次使用时才下载**）

    Args:
        allow_download: False 时只检查本地，缺失直接抛错（用于 /health、/voices、
                        warmup 等"不得自动下载"的路径）
    """
    cur = state(spec)
    if cur["ready"]:
        return Path(cur["dir"])
    if not allow_download:
        raise ModelStoreError(
            f"轻量语音模型「{spec.title}」尚未下载（需要 {spec.size_mb:.1f} MB，"
            f"下载到 {cache_root()}）。首次使用录音或朗读时会自动下载，"
            f"也可在设置页点「下载语音模型」。",
            "model_missing")
    return download(spec, progress=progress)


def get_spec(key: str) -> ModelSpec:
    if key not in MANIFEST:
        raise ModelStoreError(f"未知模型：{key}（可选：{list(MANIFEST)}）", "model_missing")
    return MANIFEST[key]


def pick(kind: str) -> ModelSpec:
    """取当前配置的语音模型（目前只有 ASR；TTS 已整体移除）"""
    if kind != "asr":
        raise ModelStoreError(f"不支持的模型类型：{kind}（TTS 已移除）", "model_missing")
    key = (os.getenv("SPEECH_ASR_MODEL_KEY") or "").strip() or DEFAULT_ASR_MODEL
    return get_spec(key)


def clear_downloads() -> int:
    """删除 downloads/ 下的压缩包（模型目录保留）；返回释放字节数"""
    d = cache_root() / "downloads"
    freed = dir_size(d)
    shutil.rmtree(d, ignore_errors=True)
    return freed


# ==================== CLI（离线准备模型用） ====================

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="轻量语音模型按需下载")
    ap.add_argument("--list", action="store_true", help="列出模型与体积/状态")
    ap.add_argument("--download", nargs="*", default=None,
                    help="下载指定 key，或 'all' / 'asr'")
    ap.add_argument("--cache-dir", default=None, help="覆盖 SPEECH_MODEL_CACHE_DIR")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.cache_dir:
        os.environ["SPEECH_MODEL_CACHE_DIR"] = args.cache_dir

    if args.list or not args.download:
        info = describe_all()
        print(f"缓存目录：{info['cache_dir']}")
        print(f"推荐下载量：{info['required_download_mb']} MB"
              f"（ASR {MANIFEST[DEFAULT_ASR_MODEL].size_mb:.1f} MB）\n")
        for m in info["models"]:
            flag = "已就绪" if m["ready"] else m["status"]
            print(f"  [{m['kind']}] {m['key']:<34} 下载 {m['download_mb']:>6.1f} MB "
                  f"占用 {m['disk_mb']:>6.1f} MB  {flag}")
            print(f"         {m['title']}")
            print(f"         许可：{m['license']}")
        return 0

    keys: List[str] = []
    for item in args.download:
        if item == "all":
            keys.extend(MANIFEST.keys())
        elif item == "asr":
            keys.append(pick(item).key)
        else:
            keys.append(item)

    def _progress(done, total, phase, speed=None):
        if phase == "download":
            pct = (done / total * 100) if total else 0
            extra = f" {speed/1024/1024:.1f} MB/s" if speed else ""
            print(f"\r  下载 {pct:5.1f}%  {done/1024/1024:7.1f}/{total/1024/1024:.1f} MB{extra}",
                  end="", flush=True)

    rc = 0
    for key in keys:
        spec = get_spec(key)
        print(f"== {spec.key}（{spec.size_mb:.1f} MB，版本 {spec.version}）==")
        try:
            path = download(spec, progress=_progress, force=args.force)
            print(f"\r  完成：{path}（占用 {dir_size(path)/1024/1024:.1f} MB）")
        except ModelStoreError as e:
            print(f"\n  失败：{e.message}（error_type={e.error_type}）")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_cli())