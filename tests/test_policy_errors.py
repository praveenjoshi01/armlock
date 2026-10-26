import httpx
import pytest

from common.exceptions import ModelUnavailableError
from harness.policies import OllamaPolicy


def _policy(handler):
    p = OllamaPolicy(model_name="m")
    real = httpx.Client

    class _C(real):
        def __init__(self, *a, **k):
            super().__init__(*a, transport=httpx.MockTransport(handler), **k)
    return p, _C


def test_malformed_tool_call_is_retried_then_recorded_not_fatal(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500, text='{"error":"XML syntax error on line 7"}')
    p, C = _policy(handler)
    monkeypatch.setattr(httpx, "Client", C)
    tc, text = p.select_action("sys", "do it", [], seed=1)
    assert tc is None and "malformed tool call" in text and len(calls) == 3


def test_real_outage_still_raises(monkeypatch):
    p, C = _policy(lambda req: httpx.Response(500, text="model runner has unexpectedly stopped"))
    monkeypatch.setattr(httpx, "Client", C)
    with pytest.raises(ModelUnavailableError):
        p.select_action("sys", "do it", [], seed=1)
