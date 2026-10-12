"""Synthetic Windows-native checks; never opens a login or the real app profile."""
import http.cookiejar
import urllib.error
import urllib.request
import json
import hashlib
import platform
import subprocess
import struct
import sys
import tempfile
from pathlib import Path


def sample_pdf(path):
    stream = b'0.5 w 50 600 250 100 re S 50 633 m 300 633 l S 50 667 m 300 667 l S 200 600 m 200 700 l S\n'
    for x,y,text in [(60,680,'Assessment'),(210,680,'Weight'),(60,647,'Essay'),(210,647,'60%'),(60,614,'Exam'),(210,614,'40%')]:
        stream += f'BT /F1 12 Tf {x} {y} Td ({text}) Tj ET\n'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream']
    data, offsets = b'%PDF-1.4\n', [0]
    for number,obj in enumerate(objects,1):
        offsets.append(len(data)); data += str(number).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    offset = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    for position in offsets[1:]: data += f'{position:010d} 00000 n \n'.encode()
    data += f'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{offset}\n%%EOF\n'.encode()
    path.write_bytes(data)


def main():
    if sys.platform != 'win32': raise SystemExit('Native checks require Windows')
    from platform_support import dpapi, helper_command
    from desktop import Backend, delete_account, windows_prerequisites
    from windows_document import read
    from winrt.windows.media.ocr import OcrEngine
    from PIL import Image
    assert struct.calcsize('P') == 8 and platform.machine().lower() in {'amd64', 'x86_64'}
    assert sys.stdout is not None, 'Native checks require the console worker interpreter'
    prerequisites = windows_prerequisites()
    import clr  # Load the required .NET Framework without opening any GUI.
    private_runtime = Path(sys.executable).parent / 'python312._pth'
    if private_runtime.is_file():
        bundle = Path(sys.executable).resolve().parent.parent
        assert sys.flags.isolated and sys.flags.utf8_mode and sys.dont_write_bytecode
        assert all(path and Path(path).resolve().is_relative_to(bundle) for path in sys.path), 'Python path escaped the private bundle'
    bundle=Path(sys.executable).resolve().parent.parent
    manifest_digest=hashlib.sha256((bundle/'payload-sha256.json').read_bytes()).hexdigest()
    synthetic = b'PolyU synthetic session verification'
    assert dpapi(dpapi(synthetic), decrypt=True) == synthetic
    with tempfile.TemporaryDirectory(prefix='polyu-native-check-') as temporary:
        root = Path(temporary)
        pdf = root / 'synthetic.pdf'
        sample_pdf(pdf)
        pages = read(pdf, text_only=True)
        assert len(pages) == 1 and 'Assessment' in pages[0]['text']
        helper = subprocess.run(helper_command('pdf_layout.py', pdf, '[1]'),
            capture_output=True, text=True, encoding='utf-8', check=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW)
        tables = json.loads(helper.stdout)
        assert tables.get('1', {}).get('tables'), 'Bundled PDF table helper did not return a table'
        image = root / 'blank.png'
        Image.new('RGB', (120,120), 'white').save(image)
        blank = read(image)
        assert blank[0]['processed'] and not blank[0].get('issues')
        assert all(n is None for n in blank[0]['rowConfidences'])
        backend = Backend(root / 'synthetic-app')
        try:
            backend.start()
            with backend.opener.open(backend.url + 'api/health', timeout=10) as response:
                assert json.load(response)['ok']
            try:
                backend.opener.open(backend.url + 'api/data', timeout=10)
                raise AssertionError('Public data read accepted')
            except urllib.error.HTTPError as error: assert error.code == 401
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            ticket = backend.request('bootstrap', {})['ticket']
            request = urllib.request.Request(backend.url + 'api/client/session',
                data=json.dumps({'ticket':ticket}).encode(), headers={'Content-Type':'application/json', 'Origin':backend.url.rstrip('/')})
            with client.open(request, timeout=10) as response: assert json.load(response)['csrf']
            with client.open(backend.url + 'api/data', timeout=10) as response:
                assert json.load(response)['courseCount'] == 0
        finally: backend.stop()
        delete_account(root / 'synthetic-app', backend)
        assert not (root / 'synthetic-app').exists()
    print(json.dumps({'ok':True, 'platform':platform.platform(), 'dpapiRoundTrip':True, 'payloadManifestSHA256':manifest_digest,
        'pdfTextAndFrozenTables':True, 'blankImageProcessed':True,
        'ocrLanguages':len(OcrEngine.available_recognizer_languages), 'ownedBackendStopped':True,
        'syntheticProfileDeleted':True, 'systemPrerequisites':prerequisites,
        'privateRuntimeIsolation':bool(private_runtime.is_file()),
        'webviewLoginValidated':False, 'realCanvasValidated':False}))


if __name__ == '__main__': main()
