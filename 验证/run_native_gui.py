"""Launch the actual candidate AppDelegate with validated temporary data and nonpersistent WebKit.

Only app-owned menus/sheets and views are exercised. No SSO, real profile, system
warning automation, Apple events, accessibility access, or external network is used.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from release_build import CONSOLE_FILES, NAME, VERSION, source_hashes


def alive(pid, group=False):
    try:
        (os.killpg if group else os.kill)(pid, 0)
        return True
    except ProcessLookupError:
        return False


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This check needs a macOS GUI session')
    build = Path(tempfile.gettempdir()) / ('PolyUStudyConsole-' + VERSION + '-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16] + '-macos')
    candidate = build / (NAME + '.app')
    info = plistlib.loads((candidate / 'Contents/Info.plist').read_bytes())
    executable = candidate / 'Contents/MacOS' / info['CFBundleExecutable']
    for name in CONSOLE_FILES:
        assert (candidate / 'Contents/Resources/console' / name).read_bytes() == (ROOT / '学习控制台' / name).read_bytes(), name
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(candidate)], check=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = ROOT / '验证/native-gui' / stamp
    output.mkdir(parents=True)
    evidence = {'at': datetime.now(timezone.utc).isoformat(), 'version': VERSION,
                'candidate': str(candidate), 'candidateExecutableSHA256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                'sourceHashes': source_hashes(), 'productionNativeAppLaunched': False,
                'productionNativeLifecycleValidated': False, 'realAccountDirectoryAccessed': False,
                'realCanvasValidated': False, 'guiLoginValidated': False, 'persistentLoginStoreValidated': False,
                'windowsNativeValidated': False, 'runs': []}
    successful = False
    with tempfile.TemporaryDirectory(prefix='polyu-native-smoke-') as temporary:
        isolated = Path(temporary)
        isolated.chmod(0o700)
        marker = isolated / '.polyu-isolated-test.json'
        nonce = str(uuid.uuid4())
        marker.write_text(json.dumps({'nonce': nonce}), encoding='utf-8')
        marker.chmod(0o600)
        control = isolated / 'control'
        control.mkdir(mode=0o700)
        owned_pids = []
        owned_groups = []
        launcher = None
        try:
            for phase in ('first', 'reopen', 'logout'):
                run_output = output / phase
                run_output.mkdir()
                report_file = control / ('report-' + phase + '.json')
                started_file = control / ('started-' + phase + '.json')
                with (run_output / 'launch.log').open('wb') as log:
                    launcher = subprocess.Popen(['open', '-n', '-W', '-a', str(candidate),
                        '--stdout', str(run_output / 'native-stdout.txt'), '--stderr', str(run_output / 'native-stderr.txt'), '--args', '--isolated-test',
                        str(isolated), '--isolation-nonce', nonce, '--gui-smoke', phase], stdout=log, stderr=log)
                    deadline = time.monotonic() + 150
                    previous_progress = None
                    while not report_file.is_file() and time.monotonic() < deadline:
                        if started_file.is_file():
                            started = json.loads(started_file.read_text(encoding='utf-8'))
                            assert Path(started['candidateBundle']).resolve() == candidate.resolve()
                            if started['nativePID'] not in owned_pids:
                                owned_pids.append(started['nativePID'])
                                evidence['productionNativeAppLaunched'] = True
                        progress_file = control / ('progress-' + phase + '.txt')
                        if progress_file.is_file():
                            progress = progress_file.read_text(encoding='utf-8')
                            if progress != previous_progress:
                                print(phase + ': ' + progress, flush=True)
                                previous_progress = progress
                        if launcher.poll() is not None:
                            break
                        time.sleep(0.2)
                    if not report_file.is_file():
                        detail = (run_output / 'native-stderr.txt').read_text(encoding='utf-8', errors='replace') if (run_output / 'native-stderr.txt').exists() else ''
                        raise RuntimeError('Candidate produced no GUI report: ' + phase + '. ' + detail[-1000:])
                    report = json.loads(report_file.read_text(encoding='utf-8'))
                    assert Path(report['candidateBundle']).resolve() == candidate.resolve()
                    assert Path(report['dataRoot']).resolve() == (isolated / 'app-data').resolve()
                    owned_groups.extend(report['backendPIDs'])
                    deadline = time.monotonic() + 15
                    while (alive(report['nativePID']) or any(alive(pid, True) for pid in report['backendPIDs'])) and time.monotonic() < deadline:
                        time.sleep(0.1)
                    report['nativeProcessExitedAfterWindowClose'] = not alive(report['nativePID'])
                    report['allOwnedBackendGroupsStopped'] = all(not alive(pid, True) for pid in report['backendPIDs'])
                    launcher.wait(timeout=10)
                    report['launchServicesExitCode'] = launcher.returncode
                    for path in control.iterdir():
                        if path.is_file() and (path.name.startswith(phase + '-') or path.name.endswith('-' + phase + '.json') or path.name == 'progress-' + phase + '.txt'):
                            shutil.copyfile(path, run_output / path.name)
                    (run_output / 'checked-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    evidence['runs'].append(report)
                    assert report['ok'], report.get('error')
                    assert report['nativeProcessExitedAfterWindowClose'] and report['allOwnedBackendGroupsStopped']
                    print(phase + ': full production GUI lifecycle passed', flush=True)
            evidence['reopenedSameTemporaryProfile'] = True
            evidence['productionNativeLifecycleValidated'] = True
            successful = True
        except Exception as error:
            evidence['error'] = str(error)
            print('GUI check failed: ' + str(error), flush=True)
        finally:
            for path in control.glob('backends-*.json'):
                owned_groups.extend(json.loads(path.read_text(encoding='utf-8'))['backendPIDs'])
            owned_groups = list(set(owned_groups))
            for pid in owned_pids:
                if alive(pid):
                    os.kill(pid, signal.SIGTERM)
            for pid in owned_groups:
                if alive(pid, True):
                    os.killpg(pid, signal.SIGTERM)
            if launcher is not None and launcher.poll() is None:
                launcher.terminate()
                launcher.wait(timeout=10)
            deadline = time.monotonic() + 5
            while (any(alive(pid) for pid in owned_pids) or any(alive(pid, True) for pid in owned_groups)) and time.monotonic() < deadline:
                time.sleep(0.1)
            for pid in owned_pids:
                if alive(pid): os.kill(pid, signal.SIGKILL)
            for pid in owned_groups:
                if alive(pid, True): os.killpg(pid, signal.SIGKILL)
            if control.exists():
                shutil.copytree(control, output / 'isolated-control', dirs_exist_ok=True)
    evidence['temporaryProfileRemoved'] = not isolated.exists()
    evidence['screenshotHashes'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in output.rglob('*.png')}
    evidence['ok'] = successful and evidence['temporaryProfileRemoved']
    (ROOT / '验证/native-gui-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print('GUI_EVIDENCE=' + str(ROOT / '验证/native-gui-evidence.json'), flush=True)
    if not evidence['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
