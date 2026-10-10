"""Snapshot the provided Python's pinned PDF reader packages, never user data."""
import hashlib
import importlib.metadata as metadata
import json
import shutil
from pathlib import Path

PACKAGES = {
    "pdfplumber": "0.11.9", "pdfminer.six": "20251230", "pypdfium2": "5.13.0",
    "Pillow": "12.3.0", "cryptography": "50.0.1", "cffi": "2.1.1",
    "charset-normalizer": "3.5.1", "pycparser": "3.0",
}


def main():
    target = Path(__file__).parent / "document-vendor"
    target.mkdir(exist_ok=True)
    manifest = {"packages": PACKAGES, "files": {}}
    for name, version in PACKAGES.items():
        dist = metadata.distribution(name)
        if dist.version != version:
            raise RuntimeError(f"Expected {name} {version}, found {dist.version}")
        for relative in dist.files or []:
            if ".." in relative.parts or relative.is_absolute() or "__pycache__" in relative.parts:
                continue
            source = Path(dist.locate_file(relative))
            if not source.is_file():
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            manifest["files"][str(relative)] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (Path(__file__).parent / "document-vendor-lock.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Pinned document dependencies:", ", ".join(PACKAGES))


if __name__ == "__main__":
    main()
