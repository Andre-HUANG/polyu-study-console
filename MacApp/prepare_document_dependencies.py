"""Restore the exact, licensed Mac reader dependencies from the public archive."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ARCHIVE_SHA256 = "a02ac8b749df11924e0e054ab933f68c614261d6d29a749e673b2bc60a45b955"


def main():
    archive = HERE / "document-vendor.zip"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("Document dependency archive checksum mismatch")
    expected = json.loads((HERE / "document-vendor-lock.json").read_text())["files"]
    for name in expected:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise RuntimeError("Unsafe document dependency path")
    target = HERE / "document-vendor"
    if target.is_symlink():
        raise RuntimeError("Document dependency destination must not be a symlink")
    if target.exists():
        actual = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}
        if actual != set(expected) or any(p.is_symlink() for p in target.rglob("*")):
            raise RuntimeError("Existing dependency folder differs; move it aside before restoring")
        for name, digest in expected.items():
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("Existing dependency checksum mismatch: " + name)
        print("Document dependencies already match the lock")
        return
    with tempfile.TemporaryDirectory(prefix=".document-vendor-", dir=HERE) as staging:
        temporary = Path(staging) / "document-vendor"
        temporary.mkdir()
        with zipfile.ZipFile(archive) as source:
            names = source.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise RuntimeError("Archive contents differ from the dependency lock")
            for name, digest in expected.items():
                data = source.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise RuntimeError("Document dependency checksum mismatch: " + name)
                destination = temporary / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
        shutil.move(str(temporary), target)
    print(f"Restored {len(expected)} locked files, including original licenses")


if __name__ == "__main__":
    main()
