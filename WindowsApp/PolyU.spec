# PyInstaller executes this file only on Windows, using the pinned build venv.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata
root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from release_build import ASSETS, CONSOLE_FILES, CONSOLE, VERSION
datas = [(str(CONSOLE / n), 'console') for n in CONSOLE_FILES]
datas += [(str(CONSOLE / 'assets' / n), 'console/assets') for n in ASSETS]
binaries, hidden = [], [Path(n).stem for n in CONSOLE_FILES if n.endswith('.py')]
hidden += ['desktop', 'windows_process', 'native_verify']
for package in ('webview', 'pythonnet', 'clr_loader', 'winrt', 'tzdata'):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hidden += imports
for line in (root / 'WindowsApp/requirements.txt').read_text(encoding='utf-8').splitlines():
    if '==' in line: datas += copy_metadata(line.split('==')[0])
analysis = Analysis([str(root / 'WindowsApp/main.py')],
    pathex=[str(CONSOLE), str(root / 'WindowsApp')], binaries=binaries, datas=datas,
    hiddenimports=hidden, excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi'],
    noarchive=False)
pyz = PYZ(analysis.pure)
# The worker has a console entry so pipe-based PDF helpers retain stdout.
gui = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='PolyUStudyConsole',
    console=False, icon=str(CONSOLE / 'assets/polyu-favicon.ico'),
    manifest=str(root / 'WindowsApp/app.manifest'), upx=False)
worker = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='PolyUWorker',
    console=True, icon=str(CONSOLE / 'assets/polyu-favicon.ico'),
    manifest=str(root / 'WindowsApp/app.manifest'), upx=False)
collect = COLLECT(gui, worker, analysis.binaries, analysis.datas, upx=False,
    name='PolyUStudyConsole-' + VERSION + '-windows-x64')
