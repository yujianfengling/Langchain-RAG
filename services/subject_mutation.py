"""Cross-process, reentrant subject mutation boundary (source and index publish)."""
import contextlib
import functools
import hashlib
import os
import threading
from pathlib import Path
from config.path_config import get_data_dir

_local = threading.local()
_locks = {}
_guard = threading.Lock()


@contextlib.contextmanager
def subject_mutation(subject_id):
    key = str(get_data_dir().resolve()) + ':' + subject_id
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, 'held', set())
        if key in held:
            yield
            return
        path = Path(get_data_dir()) / 'mutation_locks' / (hashlib.sha256(key.encode()).hexdigest() + '.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+b') as f:
            f.seek(0, 2)
            if not f.tell(): f.write(b'0'); f.flush()
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                # LK_LOCK only waits ten seconds; retry without giving up safety.
                while True:
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        continue
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX)
            _local.held = held | {key}
            try:
                yield
            finally:
                _local.held = held
                f.seek(0)
                if os.name == 'nt': msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(f, fcntl.LOCK_UN)


def coordinated(fn):
    @functools.wraps(fn)
    def wrapped(subject_id, *args, **kwargs):
        with subject_mutation(subject_id):
            return fn(subject_id, *args, **kwargs)
    return wrapped
