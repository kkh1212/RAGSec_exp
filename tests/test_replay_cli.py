from dataclasses import replace
import json
from pathlib import Path
import runpy
import subprocess
import sys
import pytest
from rag_defense import *
from rag_defense.adapters.mock import build_scenario, ANSWER, QUOTE
from rag_defense.adapters.replay import load_replay, ReplayGenerator
from rag_defense.fingerprint import to_jsonable, generation_key
from rag_defense.patch import full_document_patches, apply_patches
from rag_defense.report import save_run
from rag_defense.workspace import local_path, project_root
from rag_defense.cli import main

FIXTURE = Path("tests/fixtures/synthetic_success.jsonl")


def test_complete_synthetic_replay_reproduces_the_recorded_run():
    request, generator, observer, verifier = load_replay(FIXTURE)
    r = DefenseEngine(generator=generator, observer=observer, verifier=verifier,
                      config=DefenseConfig(search_version="v1")).repair(request)
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert r.mode == ExecutionMode.REPLAY and r.usage.generation_attempts == 10
    assert r.final_response.text == ANSWER


def test_synthetic_exporter_keeps_explicit_v1_schedule(tmp_path, capsys):
    exporter = runpy.run_path("scripts/make_synthetic_replay.py")
    path = tmp_path / "new-synthetic-replay.jsonl"
    exporter["main"](["--output", str(path)])
    request, generator, observer, verifier = load_replay(path)
    result = DefenseEngine(generator=generator, observer=observer, verifier=verifier,
                           config=DefenseConfig(search_version="v1")).repair(request)
    assert result.status == DefenseStatus.PATCH_APPLIED and result.usage.generation_attempts == 10


def test_replay_missing_modified_context_never_invents_a_response():
    s = build_scenario()
    original_key = generation_key(s.request.query, s.request.documents, s.generator.fingerprint, s.generator.version)
    generator = ReplayGenerator({original_key: s.request.initial_response},
        fingerprint=s.generator.fingerprint, version=s.generator.version)
    changed = apply_patches(s.request.documents, full_document_patches(s.request.documents[1]))
    result = generator.generate(s.request.query, changed, fresh=False, replicate_id=None)
    assert result.execution_status == ExecutionStatus.REPLAY_MISS and result.text == ""
    fresh = generator.generate(s.request.query, s.request.documents, fresh=True, replicate_id="new")
    assert fresh.execution_status == ExecutionStatus.REPLAY_MISS


def test_fresh_replay_record_can_only_be_consumed_once():
    request, generator, _, _ = load_replay(FIXTURE)
    first = generator.generate(request.query, request.documents, fresh=True, replicate_id="original_retry:1")
    second = generator.generate(request.query, request.documents, fresh=True, replicate_id="original_retry:1")
    assert first.execution_status == ExecutionStatus.OK
    assert second.execution_status == ExecutionStatus.REPLAY_MISS


def test_replay_with_only_original_generation_fails_closed(tmp_path):
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if r["kind"] != "generation" or r["input"]["replicate_id"] == "original_retry:1"]
    path = tmp_path / "original_only.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    request, g, o, v = load_replay(path)
    result = DefenseEngine(generator=g, observer=o, verifier=v,
                           config=DefenseConfig(search_version="v1")).repair(request)
    assert result.status == DefenseStatus.NO_REPAIR_FOUND and not result.patches
    assert result.final_response is request.initial_response
    assert any(t.execution_status == ExecutionStatus.REPLAY_MISS for t in result.trace)


def test_replay_tampering_and_duplicate_keys_rejected(tmp_path):
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows + [rows[-1]]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_replay(path)
    rows[0]["request"]["query"] += "changed"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_replay(path)


def test_replay_and_output_paths_cannot_escape_project(tmp_path):
    with pytest.raises(ValueError):
        local_path("../not-this-project.jsonl")
    link = tmp_path / "outside"
    link.symlink_to(project_root().parent, target_is_directory=True)
    with pytest.raises(ValueError):
        load_replay(link / "unread.jsonl")


def test_saved_run_is_metadata_only_and_has_required_trace_fields(run_scenario, tmp_path):
    s, r = run_scenario()
    path = save_run(r, tmp_path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert s.request.query not in text and ANSWER not in text and QUOTE not in text
    assert all(d.text not in text for d in s.request.documents)
    assert data["gold_correctness"] is None and data["grounding_verified"] is True
    assert data["mode"] == "mock" and data["versions"]["contract"]
    assert data["original_request_fingerprint"] and data["config"] and data["patches"]
    assert any(t["phase"] == "initial" and t["observation"] for t in data["trace"])
    assert data["trace"][-1]["reason"] == data["status"]


def test_cli_doctor_demo_and_replay_are_offline(capsys, tmp_path):
    assert main(["doctor"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["project_venv"] and not doctor["network_called"]
    assert main(["demo", "--scenario", "success", "--output-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PATCH_APPLIED"
    assert main(["replay", "--input", str(FIXTURE), "--output-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "replay"


def test_cli_rejects_live_configuration(tmp_path, capsys):
    path = tmp_path / "live.yaml"
    path.write_text('{"allow_live": true}', encoding="utf-8")
    assert main(["demo", "--config", str(path)]) == 2
    assert "configuration/input error" in capsys.readouterr().err


def test_examples_run_with_mock_callables_and_no_external_imports(capsys):
    runpy.run_path("examples/run_mock.py", run_name="__main__")
    runpy.run_path("examples/integration_example.py", run_name="__main__")
    assert capsys.readouterr().out.count("PATCH_APPLIED") == 2


def test_fresh_process_without_api_keys_or_ml_can_run_demo(tmp_path):
    root = project_root()
    env = {"PATH": str(root / ".venv/bin"), "TMPDIR": str(root / ".tmp"),
        "XDG_CACHE_HOME": str(root / ".cache"), "PYTHONPYCACHEPREFIX": str(root / ".cache/pycache"),
        "PYTHONNOUSERSITE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    code = """
import runpy, sys, socket
def blocked(*a, **k):
    raise AssertionError('network forbidden')
socket.create_connection = socket.getaddrinfo = blocked
socket.socket.connect = socket.socket.connect_ex = blocked
sys.argv = ['rag_defense.cli', 'demo', '--scenario', 'success', '--output-dir', sys.argv[1]]
try:
    runpy.run_module('rag_defense.cli', run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
assert not {'torch', 'sentence_transformers', 'sklearn'} & set(sys.modules)
"""
    done = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env, cwd=root,
                          text=True, capture_output=True, timeout=15)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["status"] == "PATCH_APPLIED"
