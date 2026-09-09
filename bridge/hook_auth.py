"""Authentication material for local Codex hook event delivery."""

from __future__ import annotations

import os
import secrets
import tempfile
import threading
from pathlib import Path


class HookTokenStore:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()

    def load_or_create(self) -> str:
        with self._lock:
            try:
                token = self.path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                token = ""
            if len(token) >= 32:
                return token
            self.path.parent.mkdir(parents=True, exist_ok=True)
            token = secrets.token_urlsafe(32)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".hook-token-", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(token + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            return token
