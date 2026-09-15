from .types import ExecutionMode, Observation, ResponseState, Readiness


class UnconfiguredObserver:
    readiness = Readiness.UNCONFIGURED
    configured = False
    version = "unconfigured-observer-v1"
    mode = ExecutionMode.MOCK

    def observe(self, query: str, response: str) -> Observation:
        raise RuntimeError("OBSERVER_NOT_CONFIGURED")


def confident(observation: Observation, threshold: float) -> Observation:
    if observation.confidence < threshold:
        return Observation(ResponseState.UNCERTAIN, observation.confidence, observation.observer_version,
                           observation.reason or "below_confidence_threshold")
    return observation
