from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    suite_root: Path
    registry_path: Path
    workspace_root: Path
    jobs_import_root: Path
    jobs_output_root: Path
    autocad_src: Path

    @property
    def sessions_root(self) -> Path:
        return self.workspace_root / "sessions"


def resolve_suite_root() -> Path:
    override = os.environ.get("TECH_OFFICE_SUITE_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    current = Path.cwd().resolve()
    if _looks_like_suite_root(current):
        return current
    if _looks_like_suite_root(current.parent):
        return current.parent
    for candidate in Path(__file__).resolve().parents:
        if _looks_like_suite_root(candidate):
            return candidate
    return Path(__file__).resolve().parents[2]


def _looks_like_suite_root(path: Path) -> bool:
    return (
        (path / "agents" / "registry.json").exists()
        and (path / "mcp" / "autocad-mcp-server").exists()
        and (path / "runtime" / "pyproject.toml").exists()
    )


def get_paths() -> RuntimePaths:
    suite_root = resolve_suite_root()
    workspace_root = suite_root / "workspace"
    return RuntimePaths(
        suite_root=suite_root,
        registry_path=suite_root / "agents" / "registry.json",
        workspace_root=workspace_root,
        jobs_import_root=workspace_root / "imports" / "jobs",
        jobs_output_root=workspace_root / "outputs" / "jobs",
        autocad_src=suite_root / "mcp" / "autocad-mcp-server" / "src",
    )


def ensure_autocad_import_path(paths: RuntimePaths | None = None) -> None:
    resolved = paths or get_paths()
    autocad_src = str(resolved.autocad_src)
    if autocad_src not in sys.path:
        sys.path.insert(0, autocad_src)
