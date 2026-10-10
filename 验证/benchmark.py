"""Same synthetic course files, real parsers/IO, and explicit simulated RTT.

No school account or network is accessed. Run this identical script against both
version roots. Temporary cold application caches become warm within each trial;
the operating system's disk cache is not purged.
"""
import argparse
import hashlib
import io
import json
import platform
import statistics
import sys
import tempfile
import time
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from unittest.mock import patch


def fixtures():
    from reportlab.pdfgen import canvas
    rows = ['Assessment methods', 'Midterm test 13 October 2026 18:30 Room ZS301',
            'Group project due 27 November 2026', 'Written examination date to be announced',
            'Midterm test 30%', 'Group project 30%', 'Written examination 40%']
    rows += [f'Lecture {i}: materials processing and laboratory discussion.' for i in range(48)]
    result = {}
    for i in range(30):
        paragraphs = ''.join('<w:p><w:r><w:t>' + row + '</w:t></w:r></w:p>' for row in rows)
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + '</w:body></w:document>'
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(zipfile.ZipInfo('word/document.xml', (2026, 10, 6, 0, 0, 0)), xml)
        result[str(100+i)] = (f'Outline {i+1}.docx', out.getvalue())
    for i in range(12):
        result[str(200+i)] = (f'Notice {i+1}.txt', '\n'.join(rows).encode())
    for i in range(4):
        out = io.BytesIO()
        pdf = canvas.Canvas(out, pagesize=(612, 792), invariant=1)
        for n in range(2):
            for j, row in enumerate(rows[:20]):
                pdf.drawString(30, 760-j*28, row)
            pdf.showPage()
        pdf.save()
        result[str(300+i)] = (f'Assessment {i+1}.pdf', out.getvalue())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--reader', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--latency-ms', type=float, default=40)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.root / 'MacApp/document-vendor'))
    sys.path.insert(0, str(args.root / '学习控制台'))
    import canvas_archive, canvas_documents, native_canvas
    payloads = fixtures()
    cfg = {'user': {'id': '1'}, 'term': {'start': '2026-09-01', 'end': '2027-08-31'},
           'courses': [{'id': '5', 'code': 'BENCH5', 'name': 'Synthetic performance course'}]}
    html = '<script>window.ENV={"current_user_id":"1"}</script><main><div class="context_module" aria-label="Materials"><ul>'
    for fid, (name, _) in payloads.items():
        html += f'<li class="context_module_item Attachment_{fid}"><a class="item_link" title="{name}" href="/courses/5/modules/items/{fid}">{name}</a></li>'
    html += '</ul></div></main>'
    html_headers = Message(); html_headers['Content-Type'] = 'text/html; charset=utf-8'
    trials = []
    for trial in range(args.trials):
        with tempfile.TemporaryDirectory(prefix='polyu-bench-') as tmp:
            profile = Path(tmp)
            api = native_canvas.session_class()(native_canvas.HOST, profile, cookies=[], persist=False)
            api.download_workers = 3  # 1.6 ignores this; 1.7 bounds its queue.
            api.fetch = lambda url, **kw: (html_headers, (html if url.endswith('/modules') else '<script>window.ENV={"current_user_id":"1"}</script><main><p>Course information.</p></main>').encode())
            class Response(io.BytesIO):
                def __init__(self, data, fid):
                    super().__init__(data)
                    self.headers = Message()
                    self.headers['Content-Type'] = 'application/octet-stream'
                    self.headers['Content-Length'] = str(len(data))
                    self.headers['ETag'] = '"fixture-' + fid + '"'
            class Opener:
                def open(self, request, **kw):
                    fid = request.full_url.split('/files/')[1].split('/')[0]
                    time.sleep(args.latency_ms/1000)
                    if request.get_header('If-none-match'):
                        raise urllib.error.HTTPError(request.full_url, 304, 'Not Modified', Message(), io.BytesIO())
                    return Response(payloads[fid][1], fid)
            for condition in ('cold', 'warm'):
                counts = {'manifestWrites': 0, 'indexWrites': 0, 'textPasses': 0, 'fullPasses': 0, 'interpretations': 0}
                original_save = native_canvas.save_private
                original_extract = canvas_documents.extract
                original_describe = canvas_documents.describe
                def save(path, value):
                    if Path(path).name == 'downloads.json': counts['manifestWrites'] += 1
                    if Path(path).name == 'index.json': counts['indexWrites'] += 1
                    return original_save(path, value)
                def extract(*a, **kw):
                    counts['textPasses' if kw.get('text_only') else 'fullPasses'] += 1
                    return original_extract(*a, **kw)
                def describe(*a, **kw):
                    counts['interpretations'] += 1
                    return original_describe(*a, **kw)
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for target, name, value in ((canvas_archive,'save_private',save), (canvas_documents,'save_private',save), (canvas_documents,'extract',extract), (canvas_documents,'describe',describe)):
                        stack.enter_context(patch.object(target,name,value))
                    if 'sync_support' in sys.modules: stack.enter_context(patch.object(sys.modules['sync_support'], 'save_private', save))
                    stack.enter_context(patch('urllib.request.build_opener', return_value=Opener()))
                    started = time.perf_counter()
                    archive = canvas_archive.archive_courses(api, profile, cfg, lambda: None, lambda _: None)
                    downloaded = time.perf_counter()
                    index = canvas_documents.scan_courses(api, profile, cfg, str(args.reader), lambda: None, lambda _: None, archive=archive)
                    finished = time.perf_counter()
                assert archive['complete'], archive['courses']
                assert len(archive['entries']) == len(payloads)
                assert all(e['sha256'] == hashlib.sha256(payloads[e['id']][1]).hexdigest() for e in archive['entries'])
                assert all(r['status'] in {'read','partial'} for r in index['records']), [(r['title'], r['status'], r['issues']) for r in index['records']]
                normalized = [{k: r.get(k) for k in ('id', 'facts', 'findings', 'status', 'issues', 'pageCount', 'processedPages')} for r in sorted(index['records'], key=lambda r: r['id'])]
                row = {'trial': trial+1, 'condition': condition, 'downloadSeconds': downloaded-started,
                       'importSeconds': finished-downloaded, 'totalSeconds': finished-started,
                       'files': len(archive['entries']), 'pages': sum(r['pageCount'] for r in index['records']),
                       'partialDocuments': sum(r['status'] == 'partial' for r in index['records']),
                       'evidenceSHA256': hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), **counts}
                trials.append(row)
                print(json.dumps(row), flush=True)
    result = {'version': json.loads((args.root/'学习控制台/release.json').read_text())['version'],
              'platform': platform.platform(), 'python': sys.version, 'readerSHA256': hashlib.sha256(args.reader.read_bytes()).hexdigest(),
              'scope': 'Synthetic account, real local file transfer/hash/parsers/PDFKit/Vision and evidence integration; simulated HTTP RTT; no real Canvas network or user account.',
              'cacheConditions': 'Cold/warm app cache in each trial; OS page cache not purged; no school cookies; same reader binary and fixtures.',
              'latencyMsPerFile': args.latency_ms,
              'fixtureHashes': {fid: {'name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)} for fid,(name,data) in payloads.items()},
              'trials': trials, 'medians': {condition: {k: statistics.median(r[k] for r in trials if r['condition'] == condition)
                         for k in ('downloadSeconds','importSeconds','totalSeconds','manifestWrites','indexWrites','textPasses','fullPasses','interpretations')}
                         for condition in ('cold','warm')}}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
