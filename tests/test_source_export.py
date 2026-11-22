"""Publishing must include schemas and code while excluding runtime/private inputs."""
import hashlib
import json
from pathlib import Path
import runpy
import tarfile

import pytest


EXPORTER = runpy.run_path("scripts/export_source.py")


def make_source_tree(root):
    for name in EXPORTER["ROOT_FILES"] + EXPORTER["DATA_FILES"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-public-fixture\n", encoding="utf-8")
    for name in ("src/rag_defense/__init__.py", "configs/defense.v2.mock.yaml", "docs/SERVER_INTEGRATION.md"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public fixture\n", encoding="utf-8")
    return root


def test_export_allowlist_omits_runtime_and_private_inputs_and_keeps_schemas(tmp_path):
    root = make_source_tree(tmp_path / "source")
    forbidden = (".env", ".env.local", ".venv/secret.py", ".cache/secret.py", "runs/record.json",
                 "models/weights.bin", "artifacts/classifier.json", "data/imported/private.jsonl",
                 "tests/fixtures/private_recording.jsonl", "src/rag_defense.egg-info/PKG-INFO")
    for name in forbidden:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic-test-private-sentinel", encoding="utf-8")
    result = EXPORTER["export_source"]("dist/source.tar.gz", root=root)
    with tarfile.open(result["archive"], "r:gz") as archive:
        names = set(archive.getnames())
        assert not {"RAGSec_exp/" + name for name in forbidden} & names
        assert "RAGSec_exp/data/templates/observer-import.schema.json" in names
        assert "RAGSec_exp/tests/fixtures/synthetic_success.jsonl" in names
        manifest = json.load(archive.extractfile("RAGSec_exp/SOURCE_MANIFEST.json"))
        for name, entry in manifest["files"].items():
            contents = archive.extractfile("RAGSec_exp/" + name).read()
            assert entry["sha256"] == hashlib.sha256(contents).hexdigest()
            assert b"synthetic-test-private-sentinel" not in contents


def test_export_refuses_symlinks_instead_of_following_sources(tmp_path):
    root = make_source_tree(tmp_path / "source")
    shared = root / "docs" / "shared.md"
    shared.symlink_to(root / "README.md")
    with pytest.raises(ValueError, match="symlink"):
        EXPORTER["export_source"]("dist/source.tar.gz", root=root)


def test_export_refuses_existing_or_outside_output(tmp_path):
    root = make_source_tree(tmp_path / "source")
    with pytest.raises(ValueError):
        EXPORTER["export_source"]("README.md", root=root)
    with pytest.raises(ValueError):
        EXPORTER["export_source"](tmp_path / "outside.tar.gz", root=root)
