from __future__ import annotations

import os


def pytest_configure() -> None:
    os.environ.setdefault("TOFFICE_RATE_LIMIT_DISABLED", "1")

