"""Resolve official Windows wheels once; assembly consumes the resulting hash lock."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "WindowsApp"
CACHE = ROOT / ".build/windows-assembly"
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "setuptools", "wheel", "packaging", "altgraph", "pefile", "pywin32-ctypes"}
ENVIRONMENT = {"implementation_name": "cpython", "implementation_version": "3.12.15", "os_name": "nt", "platform_machine": "AMD64", "platform_python_implementation": "CPython", "platform_release": "10", "platform_system": "Windows", "platform_version": "10.0.19041", "python_full_version": "3.12.15", "python_version": "3.12", "sys_platform": "win32", "extra": ""}


def metadata(name, version):
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.load(response)
    (CACHE / "metadata" / f"{canonicalize_name(name)}-{version}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main():
    (CACHE / "metadata").mkdir(parents=True, exist_ok=True)
    pins = {}
    for line in (SOURCE / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "==" not in line:
            continue
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        if name not in BUILD_ONLY:
            pins[name] = next(iter(requirement.specifier)).version
    tags = list(cpython_tags((3, 12), platforms=["win_amd64"])) + list(compatible_tags((3, 12), interpreter="cp312", platforms=["win_amd64"]))
    ranking = {tag: index for index, tag in enumerate(tags)}
    wheels, sdists, dependencies, errors = [], [], {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(metadata, name, version): (name, version) for name, version in pins.items()}
        for future in concurrent.futures.as_completed(futures):
            name, version = futures[future]
            try:
                data = future.result()
                applicable = []
                for text in data["info"].get("requires_dist") or []:
                    requirement = Requirement(text)
                    if requirement.marker and not requirement.marker.evaluate(ENVIRONMENT):
                        continue
                    dependency = canonicalize_name(requirement.name)
                    applicable.append(text)
                    if dependency not in pins or pins[dependency] not in requirement.specifier:
                        errors.append(f"Unpinned/incompatible Windows dependency: {name}: {text}; selected {pins.get(dependency)}")
                dependencies[name] = applicable
                candidates = []
                for entry in data["urls"]:
                    if entry["packagetype"] != "bdist_wheel":
                        continue
                    _, _, _, wheel_tags = parse_wheel_filename(entry["filename"])
                    matching = wheel_tags.intersection(ranking)
                    if matching:
                        candidates.append((min(ranking[tag] for tag in matching), entry))
                if candidates:
                    entry = min(candidates, key=lambda item: (item[0], item[1]["filename"]))[1]
                    wheels.append({"name": name, "version": version, "filename": entry["filename"], "url": entry["url"], "sha256": entry["digests"]["sha256"], "size": entry["size"], "metadataUrl": f"https://pypi.org/pypi/{name}/{version}/json"})
                elif name == "proxy-tools":
                    entry = next(entry for entry in data["urls"] if entry["packagetype"] == "sdist")
                    if entry["digests"]["sha256"] != "ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010":
                        raise RuntimeError("proxy_tools upstream sdist hash changed")
                    sdists.append({"name": name, "version": version, "filename": entry["filename"], "url": entry["url"], "sha256": entry["digests"]["sha256"], "size": entry["size"]})
                else:
                    errors.append(f"No CPython 3.12 Windows x64 compatible wheel: {name}=={version}")
                print(name, version, wheels[-1]["filename"] if wheels and wheels[-1]["name"] == name else "source", flush=True)
            except Exception as error:
                errors.append(f"{name}=={version}: {type(error).__name__}: {error}")
    if errors:
        raise SystemExit("\n".join(errors))
    runtime_name = "cpython-3.12.15+20261001-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    assets_url = "https://github.com/astral-sh/python-build-standalone/releases/expanded_assets/20261001"
    with urllib.request.urlopen(assets_url, timeout=60) as response:
        asset_html = response.read().decode("utf-8")
    (CACHE / "metadata/astral-20261001-assets.html").write_text(asset_html, encoding="utf-8")
    asset_start = asset_html.index(runtime_name)
    runtime_sha = re.search(r"sha256:([a-f0-9]{64})", asset_html[asset_start:asset_start + 4000]).group(1)
    lock = {"format": 1, "python": "3.12.15", "target": "x86_64-pc-windows-msvc", "resolvedEnvironment": ENVIRONMENT,
            "runtime": {"filename": runtime_name, "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20261001/" + runtime_name, "sha256": runtime_sha, "metadataUrl": assets_url},
            "wheels": sorted(wheels, key=lambda entry: entry["name"]), "sdists": sdists,
            "windowsDependencyClosure": dict(sorted(dependencies.items())),
            "buildOnlyExcluded": sorted(BUILD_ONLY)}
    (SOURCE / "runtime-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Locked", len(wheels), "Windows wheels and", len(sdists), "verified pure-Python sdist")


if __name__ == "__main__":
    main()
