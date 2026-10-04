"""线程安全的 rag 延迟导入（2026-10-03）

为什么需要：`api/app.py` 的延迟预热线程与并发到达的请求会**同时**导入 rag
（`rag` → `rag.rag_service` 等子模块）。Python 3.14 会检测到跨线程模块锁死锁并抛
`deadlock detected by _ModuleLock('rag.rag_service')`，实测导致预热失败；
若方向相反，失败的就是用户请求。

这里用一个**进程级锁**把 rag 的导入串行化：先拿到锁的线程负责导入，
其余线程等锁后直接复用已导入的模块，从而不存在两个线程同时进入导入机器的窗口。
"""
import threading
from types import ModuleType

_LOCK = threading.Lock()
_RAG: ModuleType | None = None


def load_rag() -> ModuleType:
    """返回已导入的 rag 模块（线程安全，只真正导入一次）"""
    global _RAG
    if _RAG is not None:
        return _RAG
    with _LOCK:
        if _RAG is None:
            import rag as _m          # noqa: F401  （重导入放这里，见模块 docstring）
            _RAG = _m
    return _RAG
