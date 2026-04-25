from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Iterator

from django.conf import settings


_LOCK_STALE_SECONDS = 6 * 60 * 60
_LOCK_NAME = "perfumex-email-import-" + hashlib.sha256(
    str(getattr(settings, "BASE_DIR", "")).encode("utf-8", errors="ignore")
).hexdigest()[:12] + ".lock"
_LOCK_PATH = Path(tempfile.gettempdir()) / _LOCK_NAME


def _lock_payload() -> str:
    return json.dumps(
        {
            "pid": os.getpid(),
            "started_at": time.time(),
        }
    )


def _lock_is_stale(path: Path, stale_seconds: int = _LOCK_STALE_SECONDS) -> bool:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        started_at = float(data.get("started_at") or 0)
    except Exception:
        started_at = 0
    if not started_at:
        try:
            started_at = path.stat().st_mtime
        except OSError:
            return False
    return (time.time() - started_at) > stale_seconds


def _try_remove_stale_lock(path: Path) -> bool:
    if not path.exists():
        return True
    if not _lock_is_stale(path):
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def email_import_worker_is_busy() -> bool:
    if not _LOCK_PATH.exists():
        return False
    if _try_remove_stale_lock(_LOCK_PATH):
        return False
    return _LOCK_PATH.exists()


@contextmanager
def acquire_email_import_worker_lock() -> Iterator[bool]:
    if _LOCK_PATH.exists() and _try_remove_stale_lock(_LOCK_PATH):
        pass
    fd = None
    acquired = False
    try:
        try:
            fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, _lock_payload().encode("utf-8", errors="ignore"))
            acquired = True
        except FileExistsError:
            acquired = False
        yield acquired
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if acquired:
            try:
                _LOCK_PATH.unlink()
            except OSError:
                pass
