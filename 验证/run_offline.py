"""Account-free regression runner; socket/DNS operations are blocked and recorded."""
import ast
import io
import json
import os
import platform
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'MacApp/document-vendor'))
sys.dont_write_bytecode = True
from release_build import CONSOLE_FILES, source_hashes, CONSOLE
blocked = []


def offline(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.bind'}:
        blocked.append(event)
        raise RuntimeError('Network is disabled in this offline regression run')


class ProcessIsolatedResult(unittest.TextTestResult):
    def startTest(self, test):
        # These tests normally model independent backend processes. A simulated
        # server shutdown must not leave its process-scoped stop flag in the
        # next test's fresh profile; flags remain observable inside each test.
        import runtime_cache
        runtime_cache.STOP.clear()
        runtime_cache.WAKE.clear()
        super().startTest(test)


def main():
    # Windows needs its own wheels, never arm64 Mac vendor binaries.
    if sys.platform == 'win32': sys.path.remove(str(ROOT / 'MacApp/document-vendor'))
    output = ROOT / '验证'
    for path in [CONSOLE / n for n in CONSOLE_FILES if n.endswith('.py')] + list((ROOT / 'WindowsApp').glob('*.py')):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    sys.addaudithook(offline)
    with tempfile.TemporaryDirectory(prefix='polyu-offline-') as temporary:
        os.environ.update(POLYU_CONSOLE_DATA_DIR=str(Path(temporary) / 'empty-framework'),
            POLYU_NATIVE_APP='1', POLYU_NATIVE_SECRET='synthetic-test-secret', PYTHONDONTWRITEBYTECODE='1')
        suite = unittest.TestSuite()
        for directory in ('MacApp', 'WindowsApp'):
            suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / directory), pattern='test_*.py', top_level_dir=str(ROOT / directory)))
        log = io.StringIO()
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=ProcessIsolatedResult).run(suite)
        content = log.getvalue() + '\nOFFLINE_NETWORK_ATTEMPTS=' + str(len(blocked)) + '\n'
        (output / 'test-results.txt').write_text(content, encoding='utf-8')
        evidence = {'at':datetime.now(timezone.utc).isoformat(), 'platform':platform.platform(),
            'python':sys.version, 'tests':result.testsRun, 'failures':len(result.failures), 'errors':len(result.errors),
            'skipped':len(result.skipped),
            'offlineNetworkAttempts':len(blocked), 'sourceHashes':source_hashes(),
            'windowsNativeValidated':False, 'realCanvasValidated':False}
        (output / 'test-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        print(content, end='')
        if not result.wasSuccessful() or blocked: raise SystemExit(1)


if __name__ == '__main__': main()
