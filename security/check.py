"""One local preflight entry point; failures stop every later step."""
import os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
def main():
    commands=[["security/privacy_scan.py"],["security/privacy_scan.py","--history"],
              ["security/tool_runner.py","gitleaks","git","--redact=100","--config",".gitleaks.toml","--log-opts=--all","."],
              ["验证/run_offline.py"],["验证/verify_local_api.py"]]
    if sys.platform in {"darwin","linux"}:commands.append(["security/tool_runner.py","actionlint","-shellcheck=","-pyflakes="])
    if sys.platform=="darwin":commands.append(["验证/verify_keychain.py"])
    for arguments in commands:
        subprocess.run([sys.executable,"-B",*arguments],cwd=ROOT,check=True)
    subprocess.run([sys.executable,"-B","security/privacy_scan.py","--staged"],cwd=ROOT,check=True)
if __name__=="__main__":main()
