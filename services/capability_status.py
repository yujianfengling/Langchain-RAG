"""
外部能力状态（统一探活 / 降级可见）

背景（这个项目的血泪史，见 AGENTS.md）：
- knowledge_expander 调用一个从未实现的函数，ImportError 被 except 吞掉，功能坏了一年没人发现
- CHUNK_SIZE / RAG_TOP_K / EMBEDDING_DEVICE 配了但从未被读取
- VISION_MODE=ocr 让图片永远进不了知识库
共同根因不是"代码烂"，而是：**降级只写日志，人看不见**。

这里把每个外部能力做成可查询的运行状态：
- 调用方在**真实调用之后**上报结果（被动探活，不额外发探测请求、不花钱）
- /health/ready 暴露给前端与运维，降级能第一时间被看见
- 与 /health 的 capabilities 区分：那个是"配置态"（key 配没配），
  这个是"运行态"（最近一次真实调用成没成、失败几次、什么原因）

状态只存进程内存：它是运行时事实，不是业务数据，重启清零是正确的语义。
"""

import threading
from datetime import datetime
from typing import Any, Dict

CAP_LLM = "llm"
CAP_WEB_SEARCH = "web_search"
CAP_VISION = "vision"
CAP_SPEECH = "speech"

ALL_CAPABILITIES = (CAP_LLM, CAP_WEB_SEARCH, CAP_VISION, CAP_SPEECH)

_LOCK = threading.Lock()
_STATE: Dict[str, Dict[str, Any]] = {}


def _blank(capability: str) -> Dict[str, Any]:
    return {
        "capability": capability,
        "ok": None,               # None = 本次进程还没调用过（不等于不可用）
        "reason": "尚未调用过",
        "success_count": 0,
        "fail_count": 0,
        "last_ok_at": None,
        "last_fail_at": None,
        "last_error": "",
    }


def report(capability: str, ok: bool, reason: str = "", **extra: Any) -> None:
    """上报一次真实调用的结果（被动探活）"""
    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        st = _STATE.setdefault(capability, _blank(capability))
        st["ok"] = bool(ok)
        st["reason"] = (reason or "")[:300]
        if ok:
            st["success_count"] += 1
            st["last_ok_at"] = now
        else:
            st["fail_count"] += 1
            st["last_fail_at"] = now
            st["last_error"] = (reason or "")[:300]
        if extra:
            st.update(extra)


def safe_report(capability: str, ok: bool, reason: str = "", **extra: Any) -> None:
    """上报失败绝不能影响主流程——调用方用这个就好"""
    try:
        report(capability, ok, reason, **extra)
    except Exception:
        pass


def get_status(capability: str) -> Dict[str, Any]:
    with _LOCK:
        st = _STATE.get(capability)
        return dict(st) if st else _blank(capability)


def snapshot() -> Dict[str, Any]:
    """全部能力状态（供 /health/ready 下发；不含密钥与路径）"""
    return {cap: get_status(cap) for cap in ALL_CAPABILITIES}


def degraded() -> Dict[str, str]:
    """当前处于降级状态的能力 → 原因（ok is False 才算降级）"""
    out: Dict[str, str] = {}
    with _LOCK:
        for cap, st in _STATE.items():
            if st.get("ok") is False:
                out[cap] = st.get("last_error") or st.get("reason") or "未知原因"
    return out


def reset() -> None:
    """清空状态（测试用）"""
    with _LOCK:
        _STATE.clear()


__all__ = [
    "CAP_LLM", "CAP_WEB_SEARCH", "CAP_VISION", "CAP_SPEECH", "ALL_CAPABILITIES",
    "report", "safe_report", "get_status", "snapshot", "degraded", "reset",
]
