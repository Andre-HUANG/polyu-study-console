"""Bounded download work and shared sync/checkpoint policy."""
import math
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from native_canvas import save_private

DEFAULT_SYNC_INTERVAL = 3600
MAX_DOWNLOAD_WORKERS = 3


def sync_settings(saved):
    settings = dict(saved) if isinstance(saved, dict) else {}
    interval = settings.get('syncIntervalSeconds', DEFAULT_SYNC_INTERVAL)
    valid = type(interval) in (int, float) and math.isfinite(interval) and 60 <= interval <= 86400
    legacy_default = interval == 1800 and settings.get('syncIntervalSource') != 'custom'
    if not valid or legacy_default:
        interval = DEFAULT_SYNC_INTERVAL
    settings['syncIntervalSeconds'] = int(interval)
    settings['syncIntervalSource'] = 'custom' if interval != DEFAULT_SYNC_INTERVAL or settings.get('syncIntervalSource') == 'custom' else 'default'
    return settings


class ProgressWriter:
    """Publish at most eight completions or 250 ms apart, plus final/failure flushes."""
    def __init__(self, path, cancelled):
        self.path, self.cancelled = path, cancelled
        self.last, self.pending = 0.0, 0

    def publish(self, value, force=False, prepare=lambda: None):
        self.cancelled()
        self.pending += 1
        at = time.monotonic()
        if not force and self.pending < 8 and at - self.last < .25:
            return False
        prepare()
        save_private(self.path, value)
        self.last, self.pending = at, 0
        return True


def bounded_results(function, items, workers, cancelled):
    """Never queue more than the bounded number of running file transfers.

    Exit waits for active readers to finish/observe cancellation before callers
    can delete account data. Each result is applied by the coordinator alone.
    """
    workers = min(MAX_DOWNLOAD_WORKERS, workers) if type(workers) is int else 1
    workers = max(1, workers)
    if workers == 1:
        for item in items:
            cancelled()
            yield function(item)
        return
    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='Canvas-download') as pool:
        pending = set()
        try:
            for _ in range(workers):
                item = next(iterator, None)
                if item is not None: pending.add(pool.submit(function, item))
            while pending:
                cancelled()
                done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.remove(future)
                    yield future.result()
                    cancelled()
                    item = next(iterator, None)
                    if item is not None: pending.add(pool.submit(function, item))
        finally:
            for future in pending: future.cancel()
