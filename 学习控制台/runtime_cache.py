"""Bounded read snapshots and event-driven maintenance; no credentials in revisions."""
import contextlib
import contextvars
import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path

ACTIVE = contextvars.ContextVar("polyu_read_snapshot", default=False)
WAKE = threading.Event()
STOP = threading.Event()
LOCK = threading.RLock()
CACHE = OrderedDict()
LIMIT = 64

def clear():
    with LOCK: CACHE.clear()
    WAKE.set()

@contextlib.contextmanager
def snapshot():
    token = ACTIVE.set(True)
    try: yield
    finally: ACTIVE.reset(token)

def signature(path):
    try:
        p = Path(path)
        if p.is_symlink() or p.parent.is_symlink(): return None
        s = p.stat()
        return (s.st_ino, s.st_mtime_ns, s.st_size)
    except OSError: return None

def read_json(path, default):
    path = Path(path)
    key = (str(path.resolve()), signature(path))
    if ACTIVE.get() and key[1]:
        with LOCK:
            if key in CACHE:
                CACHE.move_to_end(key)
                return CACHE[key]
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError): return default
    if ACTIVE.get() and key[1]:
        with LOCK:
            CACHE[key] = value
            while len(CACHE) > LIMIT: CACHE.popitem(last=False)
    return value

FILES = ("config.json", "login.json", "state.json", "snapshot.json", "app-settings.json",
         "assignment-meta.json", "announcements.json", "grading_schemes.json",
         "canvas-course-grades.json", "documents/index.json", "documents/downloads.json",
         "sync-status.json", "documents/timetable.json", "timetable-manual.json",
         "calendar/official.json")

def revision(profile, epoch, day):
    value = [str(Path(profile).resolve()), epoch, day,
             [(name, signature(Path(profile) / name)) for name in FILES]]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
