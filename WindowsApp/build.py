"""Native Windows entry: hash-locked runtime, dependencies and NSIS."""
import platform,subprocess,sys
from pathlib import Path
def main():
    if sys.platform!="win32" or platform.machine().lower() not in {"amd64","x86_64"}:
        raise SystemExit("Requires native Windows x64; macOS assembly is static only")
    subprocess.run([sys.executable,"-B",str(Path(__file__).with_name("assemble_windows.py")),"--download"],check=True)
if __name__=="__main__":main()
