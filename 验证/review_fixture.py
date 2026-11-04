"""Disposable real HTTP backend for the review browser regression; synthetic data only."""
import datetime as dt
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '学习控制台'))


def guard(event, args):
    if event in {'socket.connect', 'socket.bind'}:
        if not isinstance(args[1], tuple) or args[1][0] != '127.0.0.1':
            raise RuntimeError('Review fixture permits loopback only')
    if event == 'socket.getaddrinfo' and args[0] != '127.0.0.1':
        raise RuntimeError('Review fixture blocks external DNS')


def main():
    sys.addaudithook(guard)
    with tempfile.TemporaryDirectory(prefix='polyu-review-browser-') as directory:
        os.environ.update(POLYU_CONSOLE_DATA_DIR=directory, POLYU_NATIVE_APP='1',
                          POLYU_NATIVE_SECRET='synthetic-review-native', PYTHONDONTWRITEBYTECODE='1')
        import server
        from native_canvas import save_private
        profile = server.PROFILE
        today = dt.datetime.now(ZoneInfo('Asia/Hong_Kong')).date()
        day = lambda offset: (today + dt.timedelta(days=offset)).isoformat()
        source = {'documentId': 'synthetic-outline', 'title': 'Synthetic course outline.pdf',
                  'url': 'https://canvas.polyu.edu.hk/courses/90001', 'locator': '第 3 页',
                  'quote': 'Synthetic source: confirm the report date against this outline.'}
        parts = [
            {'key': 'report', 'name': 'Report', 'pattern': '^Report$', 'mode': 'single',
             'weight': None, 'conflict': True, 'alternatives': [40, 50], 'sources': [source]},
            {'key': 'final', 'name': 'Final exam', 'pattern': '^Final exam$', 'mode': 'single',
             'weight': 60, 'conflict': False, 'sources': [source]},
        ]
        index = {'completedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'records': [],
                 'courses': [{'id': '90001', 'complete': True}], 'evidence': {
            'pending': [{'id': 'report-date', 'course': 'DEMO201', 'title': 'Research proposal',
                         'kind': 'assignment', 'needsReview': True, 'reason': '文件中的日期不一致',
                         'sources': [source]}],
            'deadlines': [{'id': 'exam', 'course': 'DEMO201', 'title': 'Final exam', 'kind': 'final',
                           'date': day(8), 'pattern': '^Final exam$', 'weight': 60, 'sources': [source],
                           'time': '', 'endTime': '', 'location': '',
                           'warnings': ['文件中的时间不一致', '文件中的地点不一致']}],
            'detailConflicts': [
                {'course': 'DEMO201', 'title': 'Final exam', 'field': 'time',
                 'alternatives': ['10:00–12:00', '14:00–16:00'], 'sources': [source]},
                {'course': 'DEMO201', 'title': 'Final exam', 'field': 'location',
                 'alternatives': ['A201', 'B301'], 'sources': [source]}],
            'grading': {'DEMO201': parts},
        }}
        save_private(profile / 'config.json', {'user': {'id': 'synthetic-review'}, 'courses': [
            {'id': '90001', 'code': 'DEMO201', 'name': 'Synthetic environmental design'}]})
        save_private(profile / 'login.json', {'user_id': 'synthetic-review', 'name': 'Synthetic Student'})
        save_private(profile / 'app-settings.json', {'autoSync': False})
        save_private(profile / 'snapshot.json', {'complete': True, 'collected_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'assignments': {
                '1': {'course': 'DEMO201', 'name': 'Report', 'score': 80, 'points': 100,
                      'sub_state': 'graded', 'graded_at': dt.datetime.now(dt.timezone.utc).isoformat()},
                '2': {'course': 'DEMO201', 'name': 'Undated essay', 'points': 100,
                      'html_url': source['url'] + '/assignments/2', 'sub_state': 'unsubmitted',
                      'submission_state_observed': True},
            }})
        save_private(profile / 'documents/index.json', index)
        save_private(profile / 'grading_schemes.json', {'DEMO201': {'verified': False,
                     'conflict': True, 'components': parts, 'basis': 'documents'}})
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        def reply(value):
            print(json.dumps(value, ensure_ascii=False), flush=True)
        reply({'base': 'http://127.0.0.1:' + str(http.server_port),
               'ticket': server.ACCESS.issue(), 'today': day(0), 'date': day(3), 'examDate': day(8)})
        try:
            for line in sys.stdin:
                request = json.loads(line)
                command = request.get('command')
                if command == 'source-change':
                    index['evidence']['pending'][0]['sources'] = [dict(source, quote='Revised synthetic date source.')]
                    save_private(profile / 'documents/index.json', index)
                    server.runtime_cache.clear()
                    reply({'ok': True})
                elif command == 'inspect':
                    review_path = profile / 'documents/reviews.json'
                    export_path = profile / 'exports/polyu-schedule.ics'
                    reply({'reviews': json.loads(review_path.read_text()) if review_path.exists() else {},
                           'reviewMode': oct(review_path.stat().st_mode & 0o777) if review_path.exists() else None,
                           'ics': export_path.read_text() if export_path.exists() else '',
                           'profileExists': profile.exists()})
                elif command == 'logout':
                    server.logout_native()
                    reply({'ok': True, 'ticket': server.ACCESS.issue(), 'remainingFiles': sum(p.is_file() for p in profile.rglob('*'))})
                elif command == 'stop':
                    reply({'ok': True})
                    break
                else:
                    reply({'error': 'Unknown fixture command'})
        finally:
            http.shutdown()
            http.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    main()
