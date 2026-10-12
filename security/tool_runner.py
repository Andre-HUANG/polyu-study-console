"""Download and execute only hash-pinned public security tools."""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent


def prepare(name):
    key = name + "-" + sys.platform + "-" + platform.machine().lower()
    lock = json.loads((ROOT/"security/tools-lock.json").read_text())["tools"][key]
    target = ROOT/".security-local/tools"/key; target.mkdir(parents=True, exist_ok=True)
    archive = target / Path(lock["url"]).name
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != lock["sha256"]:
        with urllib.request.urlopen(lock["url"], timeout=120) as response: data = response.read()
        if hashlib.sha256(data).hexdigest() != lock["sha256"]: raise RuntimeError("Security tool hash mismatch")
        archive.write_bytes(data)
    executable = target/lock["executable"]
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as source: executable.write_bytes(source.read(lock["executable"]))
    else:
        with tarfile.open(archive) as source:
            member = source.getmember(lock["executable"])
            if not member.isfile(): raise RuntimeError("Unexpected security executable")
            executable.write_bytes(source.extractfile(member).read())
    executable.chmod(0o700)
    return executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tool", choices=["gitleaks","7zip","actionlint"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = [str(prepare(args.tool)), *args.arguments]
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__": sys.exit(main())
