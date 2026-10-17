"""Exercise candidate web assets in a separate, account-free native WebKit test window.

This supplementary runner does not exercise AppDelegate. Production lifecycle
verification uses run_native_gui.py with the validated isolated-test entry point.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from release_build import CONSOLE_FILES, NAME, VERSION


def stop_owned(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    try: os.killpg(process.pid, 0)
    except ProcessLookupError: return True
    return False


def main():
    if sys.platform != 'darwin': raise SystemExit('Requires a macOS GUI environment')
    build = Path(tempfile.gettempdir()) / ('PolyUStudyConsole-' + VERSION + '-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16] + '-macos')
    candidate = build / (NAME + '.app')
    resources = candidate / 'Contents/Resources'
    console = resources / 'console'
    runtime = resources / 'runtime/arm64/bin/python3'
    for name in CONSOLE_FILES:
        assert (console / name).read_bytes() == (ROOT / '学习控制台' / name).read_bytes(), name
    output = ROOT / '验证/gui-component'
    output.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONNOUSERSITE='1')
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    evidence = {'at': datetime.now(timezone.utc).isoformat(), 'version': VERSION,
        'candidateAssetsMatchSource': True, 'productionNativeAppLaunched': False,
        'productionNativeLifecycleValidated': False, 'realAccountDirectoryAccessed': False,
        'guiLoginValidated': False, 'realCanvasValidated': False, 'windowsNativeValidated': False,
        'scope': 'Supplementary dashboard component check. Production AppDelegate lifecycle is checked separately by run_native_gui.py.',
        'runs': []}
    # The harness has a unique bundle identity, nonpersistent WebKit, no login action,
    # and no production AppDelegate. The backend interpreter rejects external network.
    with tempfile.TemporaryDirectory(prefix='polyu-gui-component-') as temporary:
        root = Path(temporary)
        app = root / 'PolyUGUIComponentCheck.app'
        executable = app / 'Contents/MacOS/GUIComponentCheck'
        executable.parent.mkdir(parents=True)
        with (app / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': 'local.polyu.guicomponent.' + root.name,
                'CFBundleExecutable': executable.name, 'CFBundlePackageType': 'APPL',
                'CFBundleName': 'PolyU isolated GUI component check', 'CFBundleShortVersionString': VERSION,
                'LSMinimumSystemVersion': '13.0', 'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}, stream)
        subprocess.run(['xcrun', 'swiftc', '-O', '-target', 'arm64-apple-macos13.0',
            '-module-cache-path', str(root / 'module-cache'), str(ROOT / '验证/NativeDashboardSmoke.swift'),
            '-o', str(executable)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
        account_root = root / 'synthetic-app'
        account_root.mkdir()
        launch = '''import os,runpy,sys
def guard(event,args):
    if event in {'socket.bind','socket.connect'}:
        address=args[1]
        if not isinstance(address,tuple) or address[0]!='127.0.0.1': raise RuntimeError('External network blocked')
    if event=='socket.getaddrinfo' and args[0]!='127.0.0.1': raise RuntimeError('External DNS blocked')
sys.addaudithook(guard)
script=sys.argv[1];sys.path.insert(0,os.path.dirname(script));sys.argv=[script,'--no-open']
runpy.run_path(script,run_name='__main__')
'''
        for number in (1, 2):
            target = output / ('run-' + str(number))
            target.mkdir(exist_ok=True)
            ready = root / ('ready-' + str(number) + '.json')
            environment.update(POLYU_NATIVE_APP='1', POLYU_APP_VERSION=VERSION,
                POLYU_NATIVE_SECRET='synthetic-gui-component-secret', POLYU_CONSOLE_DATA_DIR=str(account_root),
                POLYU_CONSOLE_PORT='0', POLYU_READY_FILE=str(ready),
                POLYU_DOCUMENT_READER=str(resources / 'read-tools/DocumentReader'))
            with (target / 'backend.log').open('w', encoding='utf-8') as log:
                backend = subprocess.Popen([str(runtime), '-B', '-c', launch, str(console / 'server.py')],
                    env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                gui = None
                try:
                    deadline = time.monotonic() + 30
                    while not ready.exists():
                        if backend.poll() is not None: raise RuntimeError('Owned empty backend exited')
                        if time.monotonic() > deadline: raise RuntimeError('Owned backend not ready')
                        time.sleep(.1)
                    status = json.loads(ready.read_text(encoding='utf-8'))
                    assert status['pid'] == backend.pid and status['url'].startswith('http://127.0.0.1:')
                    gui = subprocess.Popen([str(executable), status['url'], str(target)], env=environment,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', start_new_session=True)
                    try: stdout, stderr = gui.communicate(timeout=60)
                    except subprocess.TimeoutExpired:
                        stop_owned(gui)
                        raise RuntimeError('GUI component check timed out')
                    (target / 'native-stdout.txt').write_text(stdout, encoding='utf-8')
                    (target / 'native-stderr.txt').write_text(stderr, encoding='utf-8')
                    report = target / 'component-evidence.json'
                    if not report.exists(): raise RuntimeError('GUI exited without evidence (exit ' + str(gui.returncode) + ')')
                    run = json.loads(report.read_text(encoding='utf-8'))
                    assert gui.returncode == 0 and run['ok'] and run['testWindowClosed'], run
                    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with local.open(status['url'] + 'api/data', timeout=10) as response:
                        data = json.load(response)
                    assert data['courseCount'] == 0 and not data['connection']['authorized']
                    run.update(number=number, testGUIProcessExited=True, backendStillUnauthenticated=True)
                finally:
                    if gui is not None and gui.poll() is None: stop_owned(gui)
                    backend_stopped = stop_owned(backend)
                assert backend_stopped
                run['ownedBackendProcessGroupStopped'] = backend_stopped
                evidence['runs'].append(run)
                backend_log = (target / 'backend.log').read_text(encoding='utf-8')
                assert 'External network blocked' not in backend_log and 'External DNS blocked' not in backend_log
        evidence.update(ok=True, componentReopenedWithSameSyntheticProfile=True,
            nativeDashboardComponentsValidated=True, productionNativeLifecycleValidated=False)
    evidence['syntheticProfileRemoved'] = not account_root.exists()
    (ROOT / '验证/gui-smoke-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__': main()
