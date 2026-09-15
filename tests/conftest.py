"""Every test is offline. pytest's basetemp and cache are project-local."""
import socket
import pytest
from rag_defense.adapters.mock import build_scenario
from rag_defense.engine import DefenseEngine
from rag_defense.config import DefenseConfig


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network is forbidden in the default tests")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture
def run_scenario():
    def run(name="success", **config):
        # Preserve the original v1 regression contract, including its call schedule.
        config = {"search_version": "v1", "max_additional_generation_attempts": 20, **config}
        scenario = build_scenario(name)
        result = DefenseEngine(generator=scenario.generator, observer=scenario.observer,
            verifier=scenario.verifier, config=DefenseConfig(**config)).repair(scenario.request)
        return scenario, result
    return run
