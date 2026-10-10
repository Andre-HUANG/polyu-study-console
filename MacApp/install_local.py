"""Replace only the local application bundle; never touch account storage."""
from pathlib import Path
import os
import shutil
import subprocess
import uuid

source = Path("/private/tmp/PolyUStudyConsole-build/PolyU study控制台.app")
destination = Path.home() / "Applications/PolyU study控制台.app"
destination.parent.mkdir(parents=True, exist_ok=True)
stage = destination.parent / (".PolyUStudyConsole-" + uuid.uuid4().hex + ".app")
previous = destination.parent / (".PolyUStudyConsole-previous-" + uuid.uuid4().hex + ".app")
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(source)], check=True)
shutil.copytree(source, stage, symlinks=True)
try:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(stage)], check=True)
    if destination.exists(): os.replace(destination, previous)
    try: os.replace(stage, destination)
    except Exception:
        if previous.exists(): os.replace(previous, destination)
        raise
    if previous.exists(): shutil.rmtree(previous)
finally:
    if stage.exists(): shutil.rmtree(stage)
subprocess.run(["open", str(destination)], check=True)
print("已安装并打开：" + str(destination))
