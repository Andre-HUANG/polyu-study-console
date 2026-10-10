"""Download pinned official CPython runtimes, with resumable independent ranges."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import time
import urllib.request

HERE = Path(__file__).resolve().parent / "runtime-cache"
ASSETS = {
    "arm64": ("aarch64", 25023573, "10cab8f6ed6202fdd81637aa6eda4af8d5b7eaa8fc42f9df3c6bea4923de0d93"),
}


def part(arch, start, end):
    cpu = ASSETS[arch][0]
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/20261001/cpython-3.12.15%2B20261001-{cpu}-apple-darwin-install_only_stripped.tar.gz"
    cursor = start
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"Range": f"bytes={cursor}-{end}"})
            with urllib.request.urlopen(request, timeout=90) as response:
                if response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {cursor}-"):
                    raise RuntimeError("Server did not honor the requested byte range")
                with (HERE / f"python-{arch}.tar.gz").open("r+b", buffering=0) as output:
                    output.seek(cursor)
                    while cursor <= end:
                        data = response.read(min(65536, end - cursor + 1))
                        if not data:
                            raise RuntimeError("Incomplete byte range")
                        output.write(data)
                        cursor += len(data)
            return arch
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2)


def main():
    HERE.mkdir(exist_ok=True)
    jobs = []
    for arch, (_, size, digest) in ASSETS.items():
        path = HERE / f"python-{arch}.tar.gz"
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            print(f"{arch}: already verified", flush=True)
            continue
        prefix = path.stat().st_size if path.exists() else 0
        if prefix >= size:
            path.unlink()
            prefix = 0
        path.touch(exist_ok=True)
        for start in range(prefix, size, 1024*1024):
            jobs.append((arch, start, min(size-1, start+1024*1024-1)))
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(part, *job) for job in jobs]
        for i, task in enumerate(as_completed(futures), 1):
            task.result()
            print(f"runtime download: {i}/{len(futures)} parts", flush=True)
    for arch, (_, size, digest) in ASSETS.items():
        path = HERE / f"python-{arch}.tar.gz"
        if path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Checksum mismatch: {arch}")
        print(f"{arch}: SHA256 verified", flush=True)


if __name__ == "__main__":
    main()
