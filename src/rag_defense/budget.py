from .config import DefenseConfig
from .types import TokenUsage, UsageSummary


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, config: DefenseConfig):
        self.config = config
        self.generation_attempts = self.refinement_attempts = 0
        self.grounding_verifications = self.observer_calls = self.cache_hits = 0
        self.input_tokens = self.output_tokens = 0
        self.partial_probes = self.pair_probes = 0
        self.generation_latency_ms = 0.0
        self.grounding_latency_ms = 0.0
        self.grounding_input_tokens = self.grounding_output_tokens = 0
        self.truncated = False

    @property
    def remaining(self):
        return self.config.max_additional_generation_attempts - self.generation_attempts

    def consume_generation(self, reserve: int = 0, refinement: bool = False, phase=""):
        refinement_limit = (self.config.max_refinement_probes if self.config.search_version == "v2"
                            else self.config.max_refinement_attempts)
        if self.remaining <= reserve or (refinement and
                self.refinement_attempts >= refinement_limit) or (
                phase == "partial_probe" and self.partial_probes >= self.config.max_partial_probes) or (
                phase == "pair_probe" and self.pair_probes >= self.config.max_pair_probes):
            self.truncated = True
            raise BudgetExceeded("generation or refinement budget reserved/exhausted")
        self.generation_attempts += 1
        self.refinement_attempts += int(refinement)
        self.partial_probes += int(phase == "partial_probe")
        self.pair_probes += int(phase == "pair_probe")

    def consume_verification(self):
        if self.grounding_verifications >= self.config.max_grounding_verifications:
            self.truncated = True
            raise BudgetExceeded("grounding budget exhausted")
        self.grounding_verifications += 1

    def add_tokens(self, usage: TokenUsage):
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    def summary(self):
        return UsageSummary(self.generation_attempts, self.refinement_attempts,
            self.grounding_verifications, self.observer_calls, self.cache_hits,
            self.input_tokens, self.output_tokens, self.partial_probes, self.pair_probes,
            self.generation_latency_ms, self.grounding_input_tokens, self.grounding_output_tokens,
            self.grounding_latency_ms)
