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

_LOCK = threading.RLock()
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

# 进程级可重入锁：load_rag_stack() 内部会再调 load_rag()，非重入会自锁。
_STACK: ModuleType | None = None

# 这些模块被 "from rag.X import ..." 在**模块级**引用过（最典型：
# services/knowledge_service.py 顶部的 "from rag.rag_service import"）。
# 只锁住 "import rag" 挡不住它们：任何线程导入 knowledge_service 都会直接进入 import 机器。
_STACK_MODULES = (
    "rag.rag_service",
    "rag.embedding",
    "rag.text_splitter",
    "rag.bm25_retriever",
    "rag.hybrid_retriever",
)


def load_rag_stack() -> ModuleType:
    """在进程级锁内一次性导入 rag 及被模块级引用的子模块。

    为什么不能只调 load_rag()（2026-10-07 实测事故）：
    继续导入任务 imp_9938303fadb9 在后台线程里导入 knowledge_service，触发
    "from rag.rag_service import ..."，与同时进行的预热线程并发进入 import 机器，
    Python 3.14 直接抛 deadlock detected by _ModuleLock('rag.rag_service')，
    任务以"任务执行异常"失败且 0 条目。把整条 rag 栈也放进同一把锁，
    第一次导入由先到的线程独占完成，其余线程拿到锁后直接复用 sys.modules。
    """
    global _STACK
    if _STACK is not None:
        return _STACK
    rag = load_rag()
    with _LOCK:
        if _STACK is None:
            import importlib
            for name in _STACK_MODULES:
                try:
                    importlib.import_module(name)
                except Exception:       # 可选依赖缺失不阻塞：用到时会再报真实错误
                    continue
            # knowledge_service 顶部就 import 了 rag.rag_service，一起在这里定型
            try:
                importlib.import_module("services.knowledge_service")
            except Exception:
                pass
            _STACK = rag
    return _STACK
