"""One release identity for the backend and both build pipelines."""
import json
from pathlib import Path
RELEASE = json.loads(Path(__file__).with_name("release.json").read_text(encoding="utf-8"))
VERSION = RELEASE["version"]
