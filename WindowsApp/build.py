"""Native Windows build, fixed dependencies, offline smoke check, then binary ZIP."""
import hashlib
import importlib.metadata as metadata
import json
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from release_build import NAME, VERSION, verify_bundle, source_hashes


def main():
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise SystemExit('Windows x64 原生环境才能构建，不能用 Mac 产物代替')
    if sys.version_info[:2] != (3,12): raise SystemExit('构建要求 Python 3.12 x64')
    if sys.getwindowsversion().build < 19041: raise SystemExit('需要 Windows 10 2004 或更新版本')
    requirements = ROOT / 'WindowsApp/requirements.txt'
    for line in requirements.read_text(encoding='utf-8').splitlines():
        if '==' not in line: continue
        name, version = line.strip().split('==')
        if metadata.version(name) != version: raise SystemExit('依赖版本不符合锁定文件：' + name)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
    build = ROOT / '.build/windows'
    dist = build / 'dist'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', str(dist), '--workpath', str(build / 'work'), str(ROOT / 'WindowsApp/PolyU.spec')], check=True, cwd=ROOT)
    bundle = dist / ('PolyUStudyConsole-' + VERSION + '-windows-x64')
    for name in ('PolyUStudyConsole.exe', 'PolyUWorker.exe'):
        path = bundle / name
        with path.open('rb') as binary:
            if binary.read(2) != b'MZ': raise RuntimeError('缺少真实 Windows PE 程序')
            binary.seek(0x3c); offset = struct.unpack('<I', binary.read(4))[0]
            binary.seek(offset)
            if binary.read(4) != b'PE\0\0' or struct.unpack('<H', binary.read(2))[0] != 0x8664:
                raise RuntimeError('安装包程序不是 Windows x64')
    verify_bundle(bundle)
    smoke = subprocess.run([str(bundle / 'PolyUWorker.exe'), '--platform-check'],
        check=True, capture_output=True, text=True, encoding='utf-8', timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW)
    native = json.loads(smoke.stdout)
    if not native.get('ok'): raise RuntimeError('原生 Windows 自检未通过')
    shutil.copy2(ROOT / 'WindowsApp/安装与使用.txt', bundle / '安装与使用.txt')
    shutil.copy2(ROOT / '更新说明.md', bundle / '更新说明.md')
    evidence = {'version':VERSION, 'platform':platform.platform(), 'python':sys.version,
        'sourceHashes':source_hashes(), 'packages':{d.metadata['Name']:d.version for d in metadata.distributions()},
        'nativeSmoke':native, 'guiLoginValidated':False, 'realCanvasValidated':False,
        'authenticodeSigned':False, 'releaseStatus':'candidate'}
    (bundle / 'build-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    output = ROOT / 'Win发布'
    output.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(output / (NAME + '-' + VERSION + '-windows-x64')), 'zip', dist, bundle.name))
    (output / 'SHA256.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n', encoding='utf-8')
    (output / 'build-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Windows 二进制候选包：' + str(archive))


if __name__ == '__main__': main()
