"""Per-turn cancellation/deadline boundary; late work cannot commit or emit."""
import contextlib
import queue
import sqlite3
import threading
import time

_local = threading.local()
_registry = {}
_lock = threading.RLock()


class TurnCancelled(Exception):
    pass


class TurnTimeout(TimeoutError):
    pass


class Execution:
    def __init__(self, turn_id, seconds, cancelled=None):
        self.turn_id = turn_id
        self.deadline = time.monotonic() + seconds
        self.cancelled = cancelled
        self.stop = threading.Event()
        self.reason = 'cancelled'
        self.responses = []
        self.gate = threading.RLock()
        self.phase = 'started'
        self.timings = {'started': time.monotonic()}

    def mark(self, phase):
        self.phase = phase
        if phase not in self.timings:
            self.timings[phase] = time.monotonic()
            from utils.logger import logger
            logger.info(f"[TutorPhase] turn={self.turn_id} phase={phase} elapsed={self.timings[phase] - self.timings['started']:.3f}s")

    def cancel(self, reason='cancelled'):
        self.reason = reason
        self.stop.set()
        # requests.close may wait on the reader: never block the SSE watchdog.
        for response in list(self.responses):
            threading.Thread(target=response.close, daemon=True).start()
        self.responses.clear()

    def check(self):
        if self.stop.is_set():
            if self.reason == 'timeout': raise TurnTimeout('本轮生成超时')
            raise TurnCancelled('本轮已中断')
        if self.cancelled and self.cancelled():
            self.cancel()
            raise TurnCancelled('本轮已取消')
        if time.monotonic() >= self.deadline:
            self.cancel('timeout')
            raise TurnTimeout('本轮生成超时')


def current():
    return getattr(_local, 'execution', None)


@contextlib.contextmanager
def bind(execution):
    old = current()
    _local.execution = execution
    try: yield
    finally: _local.execution = old


def register(execution):
    with _lock: _registry[execution.turn_id] = execution


def get(turn_id):
    with _lock: return _registry.get(turn_id)


def release(turn_id):
    with _lock: _registry.pop(turn_id, None)


def check():
    execution = current()
    if execution: execution.check()


def iterate(iterator, execution):
    """Consume blocking upstream in a bounded daemon; no late result escapes."""
    q = queue.Queue(maxsize=16)
    def put(item):
        while not execution.stop.is_set():
            try:
                q.put(item, timeout=.05)
                return
            except queue.Full: pass
    def consume():
        with bind(execution):
            try:
                for item in iterator:
                    execution.check()
                    put(('item', item))
                put(('end', None))
            except BaseException as e:
                put(('error', e))
            finally:
                close = getattr(iterator, 'close', None)
                if close: close()
    threading.Thread(target=consume, daemon=True).start()
    while True:
        execution.check()
        try: kind, item = q.get(timeout=.05)
        except queue.Empty: continue
        execution.check()
        if kind == 'end': return
        if kind == 'error': raise item
        yield item


def call(fn, execution):
    def result():
        yield fn()
    return next(iterate(result(), execution))


class GuardedConnection(sqlite3.Connection):
    def commit(self):
        execution = current()
        with execution.gate if execution else contextlib.nullcontext():
            check()
            return super().commit()

    def __exit__(self, typ, value, tb):
        execution = current()
        with execution.gate if execution else contextlib.nullcontext():
            if typ is None:
                try: check()
                except BaseException:
                    self.rollback()
                    raise
            return super().__exit__(typ, value, tb)


def connect(*args, **kwargs):
    kwargs['factory'] = GuardedConnection
    return sqlite3.connect(*args, **kwargs)


def gated(fn):
    import functools
    @functools.wraps(fn)
    def wrapped(turn_id, *args, **kwargs):
        execution = get(turn_id)
        with execution.gate if execution else contextlib.nullcontext():
            return fn(turn_id, *args, **kwargs)
    return wrapped
