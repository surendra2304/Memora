from __future__ import annotations
import json
import os
import tempfile
from typing import Any


class AtomicJsonFile:
    def __init__(self, path: str):
        self.path = path

    def read(self, default: Any = None) -> Any:
        if not os.path.exists(self.path):
            return default
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, value: Any) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".memora-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(value, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class AppendOnlyEventFile:
    def __init__(self, path: str):
        self.path = path

    def append(self, event: dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
