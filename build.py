"""One entry point: offline checks, then the chosen platform's release build."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--mac', action='store_true')
    action.add_argument('--windows', action='store_true')
    parser.add_argument('--download', action='store_true', help='Fetch locked official Windows runtime/build inputs when assembling on macOS')
    args = parser.parse_args()
    if args.mac and sys.platform != 'darwin': parser.error('Mac 包只能在 macOS 上构建')
    if args.windows and sys.platform not in {'win32', 'darwin'}: parser.error('Windows 包构建入口支持 Windows x64 或 macOS 官方运行时装配')
    if args.download and not (args.windows): parser.error('--download 仅用于 Windows 包')
    subprocess.run([sys.executable, '-B', str(ROOT / '验证/run_offline.py')], check=True, cwd=ROOT)
    if args.check: return
    script = ROOT / ('MacApp/build.py' if args.mac else 'WindowsApp/assemble_windows.py')
    command = [sys.executable, '-B', str(script)]
    if args.download: command.append('--download')
    subprocess.run(command, check=True, cwd=ROOT)


if __name__ == '__main__': main()
