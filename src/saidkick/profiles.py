"""On-disk profiles.

A profile is a named directory under ``$SAIDKICK_HOME/profiles/<name>/`` holding
two things:

- ``userdata/`` — a Chromium user-data-dir, used by *attached* contexts. It
  preserves everything: cookies, localStorage, sessionStorage, IndexedDB,
  service workers.
- ``storage_state.json`` — a snapshot used to *seed* ephemeral contexts. It
  carries cookies and localStorage only (a Playwright limitation), which is
  enough for most logins but not for apps that keep auth in IndexedDB.

``save_profile`` writes the ``storage_state.json``; that is how an ephemeral
context's freshly-authenticated state becomes durable.
"""

import json
import os
import re
import shutil
from pathlib import Path

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
STATE_FILE = "storage_state.json"
USERDATA_DIR = "userdata"


def default_root() -> Path:
    home = os.environ.get("SAIDKICK_HOME", "~/.saidkick")
    return Path(home).expanduser() / "profiles"


class ProfileStore:
    def __init__(self, root: Path | None = None):
        self.root = root or default_root()

    def _validate(self, name: str) -> str:
        if not _NAME_RE.match(name or ""):
            raise ValueError(
                f"invalid profile name: {name!r} (allowed: letters, digits, '-', '_')"
            )
        return name

    def path(self, name: str) -> Path:
        return self.root / self._validate(name)

    def userdata(self, name: str) -> Path:
        return self.path(name) / USERDATA_DIR

    def state_file(self, name: str) -> Path:
        return self.path(name) / STATE_FILE

    def exists(self, name: str) -> bool:
        return self.path(name).is_dir()

    def save_state(self, name: str, state: dict) -> None:
        """Write the seed atomically.

        A plain write that is interrupted leaves a truncated JSON file, and
        every future context seeded from this profile then fails to open. The
        temp-then-rename keeps the previous state readable until the new one is
        complete.
        """
        self.path(name).mkdir(parents=True, exist_ok=True)
        target = self.state_file(name)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, target)  # atomic on POSIX

    def load_state(self, name: str) -> dict | None:
        f = self.state_file(name)
        return json.loads(f.read_text()) if f.is_file() else None

    def delete(self, name: str) -> None:
        shutil.rmtree(self.path(name), ignore_errors=True)

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for child in sorted(self.root.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or not _NAME_RE.match(child.name):
                continue
            state = child / STATE_FILE
            out.append(
                {
                    "name": child.name,
                    "has_state": state.is_file(),
                    "has_userdata": (child / USERDATA_DIR).is_dir(),
                    "updated": state.stat().st_mtime if state.is_file() else None,
                }
            )
        return out
