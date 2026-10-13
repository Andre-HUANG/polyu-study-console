"""Download only locked upstream updater SDKs; no account/session transport."""
import hashlib
import json
import pathlib
import shutil
import tarfile
import urllib.request
import zipfile
ROOT=pathlib.Path(__file__).resolve().parent
LOCK=json.loads((ROOT/"updater-lock.json").read_text())

def prepare(platform):
    entry=LOCK["sparkle" if platform=="mac" else "winsparkle"]
    cache=ROOT/".build/updater";cache.mkdir(parents=True,exist_ok=True)
    path=cache/entry["filename"]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry["sha256"]:
        temporary=path.with_suffix(path.suffix+".partial")
        with urllib.request.urlopen(entry["url"],timeout=45) as response,temporary.open("wb") as output:
            shutil.copyfileobj(response,output)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=entry["sha256"]: raise RuntimeError("更新依赖哈希不匹配")
        temporary.replace(path)
    folder=cache/platform
    if folder.exists(): shutil.rmtree(folder)
    folder.mkdir()
    if platform=="mac":
        with tarfile.open(path) as archive: archive.extractall(folder,filter="data")
        return folder
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            p=pathlib.PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts: raise ValueError("更新依赖路径无效")
        archive.extractall(folder)
    return folder/("WinSparkle-"+entry["version"])

if __name__=="__main__":
    import sys
    print(prepare(sys.argv[1] if len(sys.argv)>1 else "mac"))
