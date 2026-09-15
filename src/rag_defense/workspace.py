"""File-facing utilities for this standalone project's CLI and replay loader."""
from pathlib import Path


def project_root() -> Path:
    # The development installation is editable. An installed wheel uses its caller's cwd.
    source_root = Path(__file__).resolve().parents[2]
    return source_root if (source_root / "pyproject.toml").is_file() else Path.cwd().resolve()


def local_path(path: str | Path) -> Path:
    root = project_root()
    path = Path(path)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("input/output must be inside this independent project; copy records here first")
    return resolved
