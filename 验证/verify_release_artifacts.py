"""Verify delivered ZIP/DMG contents; mount only the candidate DMG read-only."""
import hashlib
import json
import plistlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from release_build import CONSOLE, CONSOLE_FILES, ASSETS, NAME, VERSION, verify_bundle


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    report = {'version': VERSION, 'artifacts': {}, 'realCanvasValidated': False, 'windowsNativeValidated': False}
    for folder in ('Mac发布','Win发布'):
        for path in sorted((ROOT/folder).iterdir()):
            if path.suffix not in {'.dmg','.exe','.zip'}: continue
            report['artifacts'][str(path.relative_to(ROOT))] = {'sha256': sha(path), 'bytes': path.stat().st_size}
            if path.suffix != '.zip': continue
            # macOS ditto stores UTF-8 names without the ZIP language flag.
            with zipfile.ZipFile(path, metadata_encoding='utf-8') as archive:
                assert archive.testzip() is None
                if folder == 'Mac发布':
                    prefix = NAME + '.app/Contents/Resources/console/'
                    info = plistlib.loads(archive.read(NAME+'.app/Contents/Info.plist'))
                    assert info['CFBundleShortVersionString'] == VERSION
                else:
                    prefix = 'PolyUStudyConsole-' + VERSION + '-windows-x64/console/'
                    manifest = json.loads(archive.read(prefix.removesuffix('console/')+'payload-sha256.json'))
                    for name,h in manifest.items():
                        assert hashlib.sha256(archive.read(prefix.removesuffix('console/')+name)).hexdigest() == h, name
                    report['windowsPayloadHashesVerified'] = len(manifest)
                names = {name[len(prefix):] for name in archive.namelist() if name.startswith(prefix) and not name.endswith('/')}
                assert names == set(CONSOLE_FILES) | {'assets/'+n for n in ASSETS}, names
                for name in CONSOLE_FILES:
                    assert archive.read(prefix+name) == (CONSOLE/name).read_bytes(), name
                for name in ASSETS:
                    assert archive.read(prefix+'assets/'+name) == (CONSOLE/'assets'/name).read_bytes(), name
    if sys.platform == 'darwin':
        image = ROOT/'Mac发布'/(NAME+'-'+VERSION+'-arm64.dmg')
        with tempfile.TemporaryDirectory(prefix='polyu-release-verify-') as tmp:
            mount = Path(tmp)/'volume'; mount.mkdir()
            subprocess.run(['hdiutil','attach','-readonly','-nobrowse','-mountpoint',str(mount),str(image)],check=True,capture_output=True)
            try:
                app = mount/(NAME+'.app')
                subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
                info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
                assert info['CFBundleShortVersionString'] == VERSION
                resources = app/'Contents/Resources'
                verify_bundle(resources)
                for name in CONSOLE_FILES: assert (resources/'console'/name).read_bytes() == (CONSOLE/name).read_bytes(), name
                for name in ASSETS: assert (resources/'console/assets'/name).read_bytes() == (CONSOLE/'assets'/name).read_bytes(), name
                executable = app/'Contents/MacOS/PolyUConsole'
                gui = json.loads((ROOT/'验证/native-gui-evidence.json').read_text())
                assert gui['ok'] and gui['candidateExecutableSHA256'] == sha(executable)
                report.update(macDMGReadOnlyContentsVerified=True, macDMGDeepStrictSignature=True, macDMGExecutableMatchesTestedGUI=True)
            finally:
                subprocess.run(['hdiutil','detach',str(mount)],check=True,capture_output=True)
    report.update(zipCRCVerified=True, consoleAllowlistAndBytesMatch=True, officialAssetsMatch=True)
    (ROOT/'验证/release-artifacts-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__': main()
