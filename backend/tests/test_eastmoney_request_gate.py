from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

import data_job.core.eastmoney_request_gate as gate_module
from data_job.core.eastmoney_request_gate import (
    EastmoneyBudgetExceeded,
    EastmoneyCircuitOpen,
    EastmoneyLockTimeout,
    EastmoneyLockUnavailable,
    EastmoneyRateLimited,
    EastmoneyRequestError,
    EastmoneyRequestGate,
    EastmoneyStateCorrupt,
)


@dataclass
class FakeResponse:
    payload: object
    text: str = "{\"rc\":0,\"data\":{}}"
    status_code: int = 200
    headers: dict[str, str] | None = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}",
                response=self,
            )
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def make_gate(tmp_path, sessions, *, budget=520, failures=3, cooldown=21600):
    clock_value = [1_700_000_000.0]
    sleeps = []

    def clock():
        return clock_value[0]

    def sleep(seconds):
        sleeps.append(seconds)
        clock_value[0] += seconds

    def factory():
        return sessions.pop(0)

    gate = EastmoneyRequestGate(
        state_path=tmp_path / "eastmoney.json",
        lock_path=tmp_path / "eastmoney.lock",
        session_factory=factory,
        min_interval=1.5,
        jitter=0,
        daily_budget=budget,
        failure_threshold=failures,
        cooldown_seconds=cooldown,
        clock=clock,
        sleep=sleep,
    )
    return gate, sleeps, clock_value


def test_gate_reuses_session_and_persists_daily_budget(tmp_path):
    response = FakeResponse({"rc": 0, "data": {"klines": ["2026-08-25"]}})
    session = FakeSession([response, response])
    gate, sleeps, _ = make_gate(tmp_path, [session], budget=2)

    assert gate.get("https://example.test", params={})["rc"] == 0
    assert gate.get("https://example.test", params={})["rc"] == 0
    assert len(session.calls) == 2
    assert sleeps == [1.5]

    persisted = json.loads((tmp_path / "eastmoney.json").read_text())
    assert persisted["requests"] == 2
    assert persisted["consecutive_failures"] == 0

    with pytest.raises(EastmoneyBudgetExceeded):
        gate.get("https://example.test", params={})


def test_gate_reconnects_once_after_empty_or_failed_response(tmp_path):
    first = FakeSession([FakeResponse({}, text="")])
    second = FakeSession([FakeResponse({"rc": 0, "data": {"klines": ["ok"]}})])
    gate, _, _ = make_gate(tmp_path, [first, second])

    result = gate.get("https://example.test", params={})

    assert result["data"]["klines"] == ["ok"]
    assert first.closed is True
    assert len(first.calls) == 1
    assert len(second.calls) == 1


def test_gate_opens_circuit_after_three_call_failures(tmp_path):
    sessions = [FakeSession([RuntimeError("down")]) for _ in range(4)]
    sessions.append(FakeSession([FakeResponse({"rc": 0})]))
    gate, _, clock_value = make_gate(tmp_path, sessions, failures=3)

    for _ in range(3):
        with pytest.raises(EastmoneyRequestError):
            gate.get("https://example.test", params={})

    with pytest.raises(EastmoneyCircuitOpen):
        gate.get("https://example.test", params={})

    clock_value[0] += 21600
    # The circuit is allowed to probe again after the cooldown.
    assert gate.get("https://example.test", params={})["rc"] == 0


def test_gate_rejects_nonzero_rc_without_returning_payload(tmp_path):
    first = FakeSession([FakeResponse({"rc": 1, "data": None})])
    second = FakeSession([FakeResponse({"rc": 1, "data": None})])
    gate, _, _ = make_gate(tmp_path, [first, second])

    with pytest.raises(EastmoneyRequestError, match="rc=1"):
        gate.get("https://example.test", params={})


def test_gate_has_a_cross_process_lock_file(tmp_path):
    gate, _, _ = make_gate(tmp_path, [FakeSession([])])

    assert gate.lock_path == tmp_path / "eastmoney.lock"
    assert gate.lock_path.parent == tmp_path


def test_two_gate_instances_share_persistent_budget(tmp_path):
    first = FakeSession([FakeResponse({"rc": 0})])
    second = FakeSession([FakeResponse({"rc": 0})])
    gate_one, _, _ = make_gate(tmp_path, [first], budget=1)
    gate_two, _, _ = make_gate(tmp_path, [second], budget=1)

    assert gate_one.get("https://example.test", params={})["rc"] == 0
    with pytest.raises(EastmoneyBudgetExceeded):
        gate_two.get("https://example.test", params={})
    assert len(second.calls) == 0


def test_403_is_not_retried(tmp_path):
    session = FakeSession([FakeResponse({}, status_code=403)])
    gate, _, _ = make_gate(tmp_path, [session])

    with pytest.raises(EastmoneyRateLimited) as caught:
        gate.get("https://example.test", params={})

    assert caught.value.status_code == 403
    assert len(session.calls) == 1
    assert session.closed is False
    assert gate.status()["consecutive_failures"] == 1


def test_429_honors_retry_after_without_immediate_retry(tmp_path):
    session = FakeSession(
        [
            FakeResponse({}, status_code=429, headers={"Retry-After": "60"}),
            FakeResponse({"rc": 0}),
        ]
    )
    gate, _, clock_value = make_gate(tmp_path, [session])

    with pytest.raises(EastmoneyRateLimited) as caught:
        gate.get("https://example.test", params={})

    assert caught.value.status_code == 429
    assert caught.value.retry_after_seconds == 60
    assert len(session.calls) == 1
    assert gate.status()["retry_after_until"] == clock_value[0] + 60

    with pytest.raises(EastmoneyRateLimited):
        gate.get("https://example.test", params={})
    assert len(session.calls) == 1

    clock_value[0] += 60
    assert gate.get("https://example.test", params={})["rc"] == 0
    assert len(session.calls) == 2


def test_corrupt_state_fails_closed(tmp_path):
    state_path = tmp_path / "eastmoney.json"
    state_path.write_text("{not-json", encoding="utf-8")
    session = FakeSession([FakeResponse({"rc": 0})])
    gate, _, _ = make_gate(tmp_path, [session])

    with pytest.raises(EastmoneyStateCorrupt):
        gate.get("https://example.test", params={})
    assert session.calls == []


def test_daily_counter_switches_at_explicit_shanghai_midnight(tmp_path):
    tz = ZoneInfo("Asia/Shanghai")
    clock_value = [datetime(2026, 8, 26, 23, 59, tzinfo=tz).timestamp()]
    state_path = tmp_path / "eastmoney.json"
    state_path.write_text(
        json.dumps(
            {
                "date": "2026-08-26",
                "requests": 519,
                "consecutive_failures": 0,
                "circuit_open_until": 0,
                "last_request_at": 0,
                "retry_after_until": 0,
            }
        ),
        encoding="utf-8",
    )
    gate = EastmoneyRequestGate(
        state_path=state_path,
        lock_path=tmp_path / "eastmoney.lock",
        session_factory=lambda: FakeSession([]),
        clock=lambda: clock_value[0],
        timezone="Asia/Shanghai",
    )

    assert gate.status()["requests"] == 519
    clock_value[0] += 2 * 60
    status = gate.status()

    assert status["date"] == "2026-08-27"
    assert status["requests"] == 0


def test_missing_fcntl_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(gate_module, "fcntl", None)
    session = FakeSession([FakeResponse({"rc": 0})])
    gate, _, _ = make_gate(tmp_path, [session])

    with pytest.raises(EastmoneyLockUnavailable):
        gate.get("https://example.test", params={})
    assert session.calls == []


def test_lock_wait_has_a_hard_timeout(monkeypatch, tmp_path):
    class AlwaysBusyFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        @staticmethod
        def flock(_fd, _flags):
            raise BlockingIOError("busy")

    monkeypatch.setattr(gate_module, "fcntl", AlwaysBusyFcntl)
    clock_value = [1_700_000_000.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock_value[0] += seconds

    gate = EastmoneyRequestGate(
        state_path=tmp_path / "eastmoney.json",
        lock_path=tmp_path / "eastmoney.lock",
        session_factory=lambda: FakeSession([]),
        clock=lambda: clock_value[0],
        sleep=sleep,
        lock_timeout=0.25,
        lock_poll_interval=0.1,
    )

    with pytest.raises(EastmoneyLockTimeout):
        gate.status()
    assert sleeps
    assert sum(sleeps) <= 0.35
