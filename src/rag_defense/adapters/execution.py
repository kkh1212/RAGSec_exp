"""Map structured provider fields, never refusal text, to operational status."""
from ..types import ExecutionStatus


def classify_execution(*, http_status=200, error_code=None, finish_reason=None,
                       structured_block=False, text="", timed_out=False):
    if timed_out:
        return ExecutionStatus.TIMEOUT
    if (structured_block is True or error_code in ("content_filter", "content_policy_violation")
            or finish_reason == "content_filter"):
        return ExecutionStatus.PROVIDER_BLOCK
    if http_status in (401, 403):
        return ExecutionStatus.AUTH_ERROR
    if http_status == 429:
        return ExecutionStatus.RATE_LIMIT
    if error_code in ("context_length_exceeded", "input_too_long"):
        return ExecutionStatus.INPUT_TOO_LONG
    if http_status >= 400:
        return ExecutionStatus.OTHER_API_ERROR
    if finish_reason in ("length", "incomplete") or not isinstance(text, str) or not text.strip():
        return ExecutionStatus.INCOMPLETE
    return ExecutionStatus.OK


class ProviderExecutionError(Exception):
    """Optional callback exception with an already mapped, structured status."""
    def __init__(self, status):
        self.status = ExecutionStatus(status)
        if self.status == ExecutionStatus.OK:
            raise ValueError("error status cannot be OK")
        super().__init__(self.status.value)
