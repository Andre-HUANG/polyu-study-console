"""Verify built Mac components using synthetic files and a guarded loopback server."""
import http.cookiejar
import hashlib
import json
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'MacApp/document-vendor'))
sys.path.insert(0, str(ROOT / 'WindowsApp'))
sys.dont_write_bytecode = True
from release_build import CONSOLE_FILES, ASSETS, NAME, VERSION, BUILD, verify_bundle, source_hashes
from native_verify import sample_pdf


def main():
    if sys.platform != 'darwin': raise SystemExit('Mac native verification requires macOS')
    build = Path(tempfile.gettempdir()) / ('PolyUStudyConsole-' + VERSION + '-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16] + '-macos')
    app = build / (NAME + '.app')
    resources = app / 'Contents/Resources'
    console = resources / 'console'
    python = resources / 'runtime/arm64/bin/python3'
    reader = resources / 'read-tools/DocumentReader'
    metadata = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    assert metadata['CFBundleShortVersionString'] == VERSION and metadata['CFBundleVersion'] == BUILD
    assert metadata['LSMinimumSystemVersion'] == '13.0'
    assert {p.name for p in console.iterdir() if p.is_file()} == set(CONSOLE_FILES)
    for name in CONSOLE_FILES:
        assert (console / name).read_bytes() == (ROOT / '学习控制台' / name).read_bytes(), name
    for name in ASSETS:
        assert (console / 'assets' / name).read_bytes() == (ROOT / '学习控制台/assets' / name).read_bytes(), name
    verify_bundle(resources)
    for binary in (app / 'Contents/MacOS/PolyUConsole', reader, python):
        assert subprocess.check_output(['lipo', '-archs', str(binary)], text=True).strip() == 'arm64'
    environment = os.environ.copy()
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    actual_python = subprocess.check_output([str(python), '-B', '-V'], text=True, env=environment).strip()
    with tempfile.TemporaryDirectory(prefix='polyu-mac-native-') as temporary:
        root = Path(temporary)
        pdf = root / 'synthetic.pdf'; sample_pdf(pdf)
        result = subprocess.run([str(reader), '--text-only', str(pdf)], check=True,
            capture_output=True, text=True, encoding='utf-8', env=environment, timeout=60)
        pages = json.loads(result.stdout)['pages']
        assert len(pages) == 1 and 'Assessment' in pages[0]['text']
        result = subprocess.run([str(python), '-B', str(console / 'pdf_layout.py'), str(pdf), '[1]'],
            check=True, capture_output=True, text=True, encoding='utf-8', env=environment, timeout=60)
        assert json.loads(result.stdout)['1']['tables']
        from PIL import Image
        image = root / 'blank.png'; Image.new('RGB', (256,256), 'white').save(image)
        result = subprocess.run([str(reader), str(image)], check=True,
            capture_output=True, text=True, encoding='utf-8', env=environment, timeout=120)
        blank = json.loads(result.stdout)['pages']
        assert blank and blank[0].get('processed') and not blank[0].get('issues')
        ready = root / 'ready.json'
        app_root = root / 'synthetic-app'
        environment.update(POLYU_NATIVE_APP='1', POLYU_APP_VERSION=VERSION,
            POLYU_NATIVE_SECRET='synthetic-local-verification-' + 'x'*48, POLYU_CONSOLE_DATA_DIR=str(app_root),
            POLYU_DOCUMENT_READER=str(reader), POLYU_CONSOLE_PORT='0', POLYU_READY_FILE=str(ready))
        # Enforce the network boundary inside the backend interpreter too.
        launch = '''import os, runpy, sys
def guard(event,args):
    if event in {'socket.bind','socket.connect'}:
        address=args[1]
        if not isinstance(address,tuple) or address[0] != '127.0.0.1': raise RuntimeError('Only loopback allowed')
    if event == 'socket.getaddrinfo' and args[0] != '127.0.0.1': raise RuntimeError('External DNS disabled')
sys.addaudithook(guard)
script=sys.argv[1]
sys.path.insert(0,os.path.dirname(script))
sys.argv=[script,'--no-open']
runpy.run_path(script,run_name='__main__')
'''
        log = (root / 'backend.log').open('w', encoding='utf-8')
        process = subprocess.Popen([str(python), '-B', '-c', launch, str(console / 'server.py')],
            env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                if process.poll() is not None: raise RuntimeError('Synthetic backend exited')
                if time.monotonic() > deadline: raise RuntimeError('Synthetic backend did not become ready')
                time.sleep(.1)
            status = json.loads(ready.read_text())
            assert status['pid'] == process.pid
            url = status['url']
            assert url.startswith('http://127.0.0.1:')
            with local.open(url + 'api/health', timeout=10) as response:
                assert json.load(response)['ok']
                assert response.headers['Server'].startswith('PolyUStudyConsole/' + VERSION)
            try:
                local.open(url + 'api/data', timeout=10)
                raise AssertionError('Unauthenticated data read allowed')
            except urllib.error.HTTPError as error: assert error.code == 401
            request = urllib.request.Request(url + 'api/native/bootstrap', data=b'{}',
                headers={'Content-Type':'application/json', 'X-PolyU-Native':environment['POLYU_NATIVE_SECRET']})
            with local.open(request, timeout=10) as response: ticket = json.load(response)['ticket']
            request = urllib.request.Request(url + 'api/client/session', data=json.dumps({'ticket':ticket}).encode(),
                headers={'Content-Type':'application/json', 'Origin':url.rstrip('/')})
            with local.open(request, timeout=10) as response: assert json.load(response)['csrf']
            with local.open(url + 'api/data', timeout=10) as response:
                data = json.load(response)
            assert data['version'] == VERSION and data['courseCount'] == 0
            assert data['events'] == [] and not data['connection']['authorized']
            assert data['freshness']['state'] == 'missing'
            with local.open(url, timeout=10) as response:
                assert '来源：Canvas' in response.read().decode('utf-8')
            try:
                local.open(url + 'api/documents/search?q=synthetic', timeout=10)
                raise AssertionError('Unauthenticated document search was allowed')
            except urllib.error.HTTPError as error: assert error.code == 401
            request = urllib.request.Request(url + 'api/native/session', data=b'{"cookies":[]}',
                headers={'Content-Type':'application/json', 'X-PolyU-Native':'wrong-secret'}, method='POST')
            try:
                local.open(request, timeout=10)
                raise AssertionError('Invalid native secret was accepted')
            except urllib.error.HTTPError as error: assert error.code == 403
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=10)
            log.close()
        backend_log = (root / 'backend.log').read_text()
        assert 'Only loopback allowed' not in backend_log and 'External DNS disabled' not in backend_log
    evidence = {'at':datetime.now(timezone.utc).isoformat(), 'version':VERSION, 'bundledPython':actual_python, 'appAndReaderArchitecture':'arm64',
        'candidate':str(app), 'sourceHashes':source_hashes(),
        'binaryHashes':{str(binary.relative_to(app)):hashlib.sha256(binary.read_bytes()).hexdigest()
                        for binary in (app / 'Contents/MacOS/PolyUConsole', reader, python)},
        'consoleAllowlistMatchesSource':True, 'officialAssetsMatchSource':True, 'bundleHasNoAccountData':True,
        'pdfKitText':True, 'bundledPdfTables':True, 'visionBlankImageProcessed':True,
        'loopbackEmptyFramework':True, 'unauthorizedSearchRejected':True, 'invalidNativeSecretRejected':True,
        'backendStopped':True, 'externalNetworkDisabled':True,
        'guiLoginValidated':False, 'realCanvasValidated':False, 'windowsNativeValidated':False}
    target = ROOT / '验证/mac-smoke-evidence.json'
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    build_evidence = ROOT / 'Mac发布/build-evidence.json'
    if build_evidence.exists():
        current = json.loads(build_evidence.read_text(encoding='utf-8'))
        current['nativeSmoke'] = evidence
        build_evidence.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__': main()
