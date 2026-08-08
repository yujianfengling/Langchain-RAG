"""
知识图谱后台任务管理器

解决：知识图谱构建同步阻塞后端事件循环 + 自动触发付费 LLM 调用的问题。

设计：
- 有界线程池（默认单 worker），构建函数在线程中执行，不阻塞 FastAPI 事件循环
- 线程安全状态机（idle / queued / building / success / failed / interrupted）
- 防重复构建：同一学科已 queued/building 时不启动第二个任务
- 全局并发上限：同时只允许一个图谱构建任务
- 后端重启后遗留的 building/queued 状态自动恢复为 interrupted
"""

import threading
import logging
from typing import Dict, Any, Optional, Callable, Tuple

logger = logging.getLogger(__name__)

# 全局同时执行的知识图谱构建任务数上限
MAX_CONCURRENT_GRAPH_BUILDS = 1


class GraphBuildManager:
    """知识图谱构建任务管理器（线程安全）"""

    def __init__(self, max_workers: int = 1):
        self._lock = threading.Lock()
        self._status: Dict[str, Dict[str, Any]] = {}
        self._executor = None
        self._max_workers = max_workers
        self._active_count = 0

    def _ensure_executor(self):
        if self._executor is None:
            import concurrent.futures
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="graph-build")
        return self._executor

    def get_status(self, subject_id: str) -> Dict[str, Any]:
        """获取学科图谱构建状态（未提交过返回 idle）"""
        with self._lock:
            st = self._status.get(subject_id)
            if st is None:
                return {"subject_id": subject_id, "status": "idle"}
            return dict(st)

    def submit(self, subject_id: str, build_fn: Callable[[str, Optional[Callable]], Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        提交图谱构建任务

        Returns:
            (accepted, status)
            - accepted=True: 任务已提交
            - accepted=False: 未接受（已构建中 / 全局忙），status 含原因
        """
        with self._lock:
            existing = self._status.get(subject_id, {}).get("status")
            if existing in ("queued", "building"):
                return False, dict(self._status[subject_id])
            if self._active_count >= self._max_workers:
                return False, {
                    "subject_id": subject_id,
                    "status": "busy",
                    "message": "已有知识图谱生成任务正在进行，请稍后再试",
                }
            self._status[subject_id] = {
                "subject_id": subject_id,
                "status": "queued",
                "processed_chunks": 0,
                "total_chunks": 0,
                "message": "任务已提交",
                "error": None,
            }
            self._active_count += 1

        def _progress(processed: int, total: int):
            with self._lock:
                st = self._status.get(subject_id)
                if st:
                    st["processed_chunks"] = processed
                    st["total_chunks"] = total
                    st["message"] = "正在抽取知识关系"

        executor = self._ensure_executor()
        future = executor.submit(self._run, subject_id, build_fn, _progress)
        future.add_done_callback(lambda f: self._on_done(subject_id, f))
        return True, dict(self._status[subject_id])

    def _run(self, subject_id: str, build_fn: Callable, progress: Callable):
        """在线程池中执行实际构建（同步阻塞函数，不放事件循环）"""
        with self._lock:
            st = self._status.get(subject_id)
            if st:
                st["status"] = "building"
                st["message"] = "正在抽取知识关系"
        try:
            logger.info(f"Graph build started: subject={subject_id}")
            build_fn(subject_id, progress)
            with self._lock:
                st = self._status.get(subject_id)
                if st:
                    st["status"] = "success"
                    st["message"] = "知识图谱生成完成"
            logger.info(f"Graph build finished: subject={subject_id}")
        except Exception as e:
            # 图谱失败绝不影响 RAG 主链路，只标记 failed
            logger.warning(f"Graph build failed: subject={subject_id}, error={e}")
            with self._lock:
                st = self._status.get(subject_id)
                if st:
                    st["status"] = "failed"
                    st["error_type"] = "build_error"
                    st["message"] = "知识图谱生成失败"
                    st["error"] = str(e)[:200]

    def _on_done(self, subject_id: str, future):
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
            st = self._status.get(subject_id)
            if st and st.get("status") in ("queued", "building"):
                # future 异常兜底（不应发生，_run 已捕获），置 failed
                st["status"] = "failed"
                st["message"] = "知识图谱生成失败"

    def reset_stale(self) -> None:
        """后端重启时调用：将遗留的 building/queued 恢复为 interrupted"""
        with self._lock:
            for sid, st in self._status.items():
                if st.get("status") in ("building", "queued"):
                    st["status"] = "interrupted"
                    st["message"] = "任务被中断（服务重启）"


# 全局单例
_manager: Optional[GraphBuildManager] = None
_manager_lock = threading.Lock()


def get_graph_manager() -> GraphBuildManager:
    """获取全局任务管理器单例"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = GraphBuildManager(max_workers=MAX_CONCURRENT_GRAPH_BUILDS)
    return _manager
