"""Program-only update transaction. Never reads or backs up account data."""
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import time
import zipfile

MAX_EXPANSION=2*1024*1024*1024
FORBIDDEN={"account","browser","webview","documents","login.json","config.json","state.json","canvas-cookies.json","grading_schemes.json"}

def digest(path):
    value=hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for part in iter(lambda:f.read(1024*1024),b""):value.update(part)
    return value.hexdigest()

def safe_program(path):
    path=pathlib.Path(path).absolute()
    if path==path.parent or path.name.lower() in {"account","windows","users","program files","runtime"}: raise ValueError("安装位置无效")
    for part in (path,*path.parents):
        if part.is_symlink() or getattr(part,"is_junction",lambda:False)(): raise ValueError("安装位置不能是链接")
    if not (path/"WindowsApp/main.py").is_file() or not (path/"runtime/python.exe").is_file(): raise ValueError("此位置不是本应用")
    if any(p.name.lower() in FORBIDDEN for p in path.rglob("*")): raise ValueError("程序目录含账号资料，不能安全自动替换")
    return path

def unpack_payload(archive,stage,expected_version):
    stage=pathlib.Path(stage);stage.mkdir()
    with zipfile.ZipFile(archive) as package:
        members=package.infolist();seen=set();total=0
        for item in members:
            name=item.filename;p=pathlib.PurePosixPath(name)
            if p.is_absolute() or not p.parts or ".." in p.parts or "\\" in name or ":" in name: raise ValueError("更新包含不安全路径")
            if any(part.lower() in FORBIDDEN for part in p.parts): raise ValueError("更新包含账号资料")
            if any(part.rstrip(". ")!=part for part in p.parts): raise ValueError("Windows 更新路径不规范")
            if stat.S_ISLNK(item.external_attr>>16): raise ValueError("更新包不能含符号链接")
            normalized=name.rstrip("/").casefold()
            if normalized in seen: raise ValueError("更新包路径重复")
            seen.add(normalized);total+=item.file_size
            if total>MAX_EXPANSION or item.file_size>512*1024*1024 or item.file_size>max(item.compress_size,1)*2000: raise ValueError("更新包过大")
        package.extractall(stage)
    roots=list(stage.iterdir())
    if len(roots)!=1 or not roots[0].is_dir(): raise ValueError("更新包根目录无效")
    root=roots[0]
    release=json.loads((root/"console/release.json").read_text())
    if release.get("version")!=expected_version or "Windows-x64" not in release.get("targets",[]): raise ValueError("更新版本或架构不匹配")
    manifest=json.loads((root/"payload-sha256.json").read_text())
    if not isinstance(manifest,dict): raise ValueError("更新文件清单无效")
    actual={p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual!=set(manifest)|{"payload-sha256.json"}: raise ValueError("更新包文件清单不完整")
    for relative,expected in manifest.items():
        if not isinstance(expected,str) or digest(root/relative)!=expected: raise ValueError("更新包内部文件校验失败")
    safe_program(root)
    return root

def replace_portable(target,new,backup):
    target=safe_program(target);new=safe_program(new);backup=pathlib.Path(backup)
    if backup.exists(): raise ValueError("回退目录已存在")
    target.rename(backup)
    try: new.rename(target)
    except BaseException:
        backup.rename(target);raise

def restore(target,backup):
    target=pathlib.Path(target);backup=safe_program(backup)
    failed=target.with_name(target.name+".failed-update")
    if failed.exists(): raise ValueError("上次失败目录需人工处理")
    if target.exists(): target.rename(failed)
    try: backup.rename(target)
    except BaseException:
        if failed.exists(): failed.rename(target)
        raise
    if failed.exists(): shutil.rmtree(failed)

def run_helper(spec):
    import sys
    sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
    from updater import verify_metadata
    root=pathlib.Path(__file__).resolve().parent
    metadata=verify_metadata((root/"updates.json").read_bytes(),(root/"updates.json.sig").read_text(),json.loads((root/"updater-lock.json").read_text())["publicKey"])
    asset=metadata["artifacts"][spec["asset"]]
    payload=root/spec["payload"]
    if payload.parent!=root or digest(payload)!=asset["sha256"]: raise ValueError("更新下载校验失败")
    target=safe_program(spec["target"])
    # The original UI/backend must finish before program files are replaced.
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.OpenProcess(0x00100000,False,int(spec["pid"]))
    if handle:
        try:
            if kernel.WaitForSingleObject(handle,60000)!=0: raise RuntimeError("旧版程序仍在运行")
        finally: kernel.CloseHandle(handle)
    backup=target.with_name(target.name+".previous-"+metadata["version"])
    if backup.exists(): raise ValueError("旧版程序回退目录已存在")
    if spec["mode"]=="portable":
        new=unpack_payload(payload,root/"payload",metadata["version"])
        if shutil.disk_usage(target.parent).free<sum(p.stat().st_size for p in new.rglob("*") if p.is_file())+100*1024*1024: raise OSError("磁盘空间不足")
        replace_portable(target,new,backup)
    elif spec["mode"]=="installed":
        expected=pathlib.Path(os.environ["LOCALAPPDATA"])/"Programs/PolyUStudyConsole"
        if target.resolve()!=expected.resolve(): raise ValueError("安装版位置与本机注册不一致")
        shutil.copytree(target,backup)
        try:
            result=subprocess.run([str(payload),"/S"],timeout=180)
            if result.returncode: raise RuntimeError("安装程序未完成")
        except BaseException:
            restore(target,backup)
            raise
    else: raise ValueError("更新模式无效")
    # The newly installed app signals only after its native window/backend load.
    ready=root/"new-version-ready"
    environment=os.environ.copy();environment["POLYU_UPDATE_ACK"]=str(ready)
    try:
        process=subprocess.Popen([str(target/"runtime/pythonw.exe"),"-I","-B","-X","utf8",str(target/"WindowsApp/main.py")],env=environment)
    except BaseException:
        restore(target,backup)
        raise
    limit=time.monotonic()+90
    while time.monotonic()<limit and not ready.exists() and process.poll() is None: time.sleep(.25)
    if ready.exists() and ready.read_text()==metadata["version"]:
        # Keep one program backup through this successful launch, then clean it.
        shutil.rmtree(backup)
        return
    if process.poll() is None:
        subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True)
    restore(target,backup)
    subprocess.Popen([str(target/"runtime/pythonw.exe"),"-I","-B","-X","utf8",str(target/"WindowsApp/main.py")])
    raise RuntimeError("新版未能启动，已恢复上一版程序；账号资料保持原样")

if __name__=="__main__":
    import sys
    try:
        run_helper(json.loads(pathlib.Path(sys.argv[1]).read_text()))
    except Exception as error:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,"更新未完成："+str(error),"PolyU 软件更新",0x10)
        except Exception: pass
        raise SystemExit(1)
