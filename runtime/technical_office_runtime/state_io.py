from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text through a temporary file, then atomically replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: PermissionError | None = None
        for attempt in range(6):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(payload, indent=indent, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path, *, default: Any = None) -> Any:
    """JSON dosyasını okur; yoksa veya bozuksa *default* döner.

    FileNotFoundError ve JSONDecodeError sessizce default ile sonuçlanır.
    Beklenmedik I/O hataları (PermissionError vb.) loglanır ama yine default döner.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        _log.warning("state_io_json_decode_error path=%s error=%s", path, exc)
        return default
    except OSError as exc:
        _log.warning("state_io_read_error path=%s error=%s", path, exc)
        return default
