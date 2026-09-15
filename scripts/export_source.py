"""Export an explicit public-source allowlist; never walk runtime/data stores."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (".gitignore", "AGENTS.md", "README.md", "pyproject.toml")
PATTERNS = ("src/rag_defense/**/*.py", "tests/*.py", "docs/*.md", "configs/*.yaml", "configs/*.json",
            "scripts/*.py", "scripts/*.sh", "examples/*.py")
DATA_FILES = ("tests/fixtures/synthetic_success.jsonl", "data/imported/README.md",
              "data/templates/observer-import.schema.json", "data/templates/observer-review.csv",
              "data/templates/evaluation-record.schema.json")


def source_files(root=ROOT):
    root = root.resolve()
    files = {root / name for name in ROOT_FILES + DATA_FILES}
    for pattern in PATTERNS:
        files.update(root.glob(pattern))
    for path in sorted(files):
        # Reject symlink components even when they currently resolve inside root.
        relative = path.relative_to(root)
        if any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise ValueError("source export refuses symlinks")
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("source file missing or outside project")
        yield path


def export_source(output, *, root=ROOT):
    root = root.resolve()
    output = Path(output)
    output = (root / output if not output.is_absolute() else output).resolve()
    if not output.is_relative_to(root) or output.exists():
        raise ValueError("choose a new output path inside the project")
    files = list(source_files(root))
    manifest = {"format": 1, "kind": "source_only_no_real_data_or_models", "files": {}}
    payloads = []
    for path in files:
        data = path.read_bytes()
        name = path.relative_to(root).as_posix()
        manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        payloads.append((name, data, 0o644))
    payloads.append(("SOURCE_MANIFEST.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), 0o644))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for name, data, mode in payloads:
            entry = tarfile.TarInfo("RAGSec_exp/" + name)
            entry.size, entry.mode, entry.mtime = len(data), mode, 0
            archive.addfile(entry, io.BytesIO(data))
    return {"archive": str(output), "files": len(files), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/rag-defense-v2-source.tar.gz")
    args = parser.parse_args(argv)
    print(json.dumps(export_source(args.output)))


if __name__ == "__main__":
    main()
