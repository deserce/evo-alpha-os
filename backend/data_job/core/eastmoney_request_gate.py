"""Shared, serialized access to Eastmoney HTTP endpoints.

The sector collector used to rotate through several Eastmoney hosts and retry
each request many times.  That multiplies traffic exactly when the source is
already refusing the client.  This module keeps one reusable ``requests``
session and coordinates all callers through a small state file plus an OS
file lock.  It deliberately does not manufacture a response when the source
returns an empty payload.
"""

from __future__ import annotations

import json
import errno
import math
import os
import random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

import requests

try:  # pragma: no cover - the production platform is macOS/Linux.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


class EastmoneyGateError(RuntimeError):
    """Base error for the shared Eastmoney request gate."""


class EastmoneyCircuitOpen(EastmoneyGateError):
    """The source has failed repeatedly and is temporarily being skipped."""


class EastmoneyBudgetExceeded(EastmoneyGateError):
    """The bounded per-day request budget has been consumed."""


class EastmoneyRequestError(EastmoneyGateError):
    """A request failed after the one permitted reconnect retry."""


class EastmoneyRateLimited(EastmoneyRequestError):
    """Eastmoney returned a rate-limit/access-denied response.

    These responses are deliberately not retried immediately.  A 429 may
    include ``Retry-After``; the value is persisted in the shared gate state
    and all callers are blocked until that timestamp.
    """

    def __init__(
        self,
        status_code: int,
        *,
        retry_after_seconds: float = 0.0,
        retry_after_until: float = 0.0,
        failure_recorded: bool = False,
    ) -> None:
        self.status_code = int(status_code)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))
        self.retry_after_until = max(0.0, float(retry_after_until))
        self.failure_recorded = bool(failure_recorded)
        message = f"Eastmoney HTTP {self.status_code}; no immediate retry"
        if self.retry_after_seconds:
            message += f"; retry after {self.retry_after_seconds:.0f}s"
        super().__init__(message)


class EastmoneyEmptyResponse(EastmoneyRequestError):
    """The HTTP response had no usable JSON body."""


class EastmoneyStateCorrupt(EastmoneyGateError):
    """The persistent gate state cannot be trusted; fail closed."""


class EastmoneyLockUnavailable(EastmoneyGateError):
    """The required OS file-lock implementation is unavailable."""


class EastmoneyLockTimeout(EastmoneyGateError):
    """A cross-process lock could not be acquired within the bound."""


class _FileLock:
    """Bounded cross-process lock around the gate state and request."""

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.path = path
        self.timeout = max(0.0, float(timeout))
        self.poll_interval = max(0.01, float(poll_interval))
        self.clock = clock
        self.sleep = sleep
        self._handle = None
        self._acquired = False

    def __enter__(self):
        if fcntl is None:
            raise EastmoneyLockUnavailable("fcntl is required for the Eastmoney gate")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self.path.open("a+")
        except OSError as exc:
            raise EastmoneyLockUnavailable(
                f"cannot open Eastmoney lock file: {self.path}"
            ) from exc

        deadline = self.clock() + self.timeout
        try:
            while True:
                try:
                    fcntl.flock(
                        self._handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    self._acquired = True
                    return self
                except BlockingIOError:
                    pass
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise EastmoneyLockUnavailable(
                            f"cannot acquire Eastmoney lock: {self.path}"
                        ) from exc

                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise EastmoneyLockTimeout(
                        f"Eastmoney lock busy after {self.timeout:.1f}s: {self.path}"
                    )
                self.sleep(min(self.poll_interval, remaining))
        except Exception:
            self._handle.close()
            self._handle = None
            raise

    def __exit__(self, exc_type, exc, tb):
        if self._handle is None:
            return False
        try:
            if self._acquired:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
            self._acquired = False
        return False


class EastmoneyRequestGate:
    """Serialize and bound requests to Eastmoney.

    ``get`` performs at most one extra attempt after rebuilding the Session.
    A failed *call* increments the persistent consecutive-failure counter once
    (not each internal attempt); three failed calls open the circuit for six
    hours.  The file lock intentionally covers the HTTP request itself so two
    independent collector processes cannot issue requests concurrently.
    """

    DEFAULT_MIN_INTERVAL = 1.5
    DEFAULT_JITTER = 0.5
    DEFAULT_DAILY_BUDGET = 520
    DEFAULT_FAILURE_THRESHOLD = 3
    DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
        lock_path: str | Path | None = None,
        session: requests.Session | None = None,
        session_factory: Callable[[], requests.Session] | None = None,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        jitter: float = DEFAULT_JITTER,
        daily_budget: int = DEFAULT_DAILY_BUDGET,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        timeout: float = 30,
        timezone: str = "Asia/Shanghai",
        lock_timeout: float = 30.0,
        lock_poll_interval: float = 0.1,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        env_state_path = os.environ.get("EVOALPHA_EASTMONEY_GATE_STATE")
        resolved_state = Path(state_path or env_state_path or backend_dir / "data/cache/eastmoney_request_gate.json")
        self.state_path = resolved_state.expanduser()
        self.lock_path = Path(lock_path or f"{self.state_path}.lock").expanduser()
        self.min_interval = max(0.0, float(min_interval))
        self.jitter = max(0.0, float(jitter))
        self.daily_budget = max(1, int(daily_budget))
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.timeout = max(1.0, float(timeout))
        self._timezone = ZoneInfo(timezone)
        self.lock_timeout = max(0.0, float(lock_timeout))
        self.lock_poll_interval = max(0.01, float(lock_poll_interval))
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._random = random_fn or random.random
        self._session_factory = session_factory or requests.Session
        self._session = session or self._new_session()

    @property
    def session(self) -> requests.Session:
        """Return the reusable Session, primarily for diagnostics/tests."""

        return self._session

    def _new_session(self) -> requests.Session:
        session = self._session_factory()
        # Direct Eastmoney calls must not accidentally inherit a system proxy
        # that is unavailable to the local scheduler.
        try:
            session.trust_env = False
        except AttributeError:
            pass
        return session

    def _reset_session(self) -> None:
        old_session = self._session
        try:
            old_session.close()
        except Exception:
            pass
        self._session = self._new_session()

    @property
    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), self._timezone).date().isoformat()

    def _default_state(self) -> dict[str, Any]:
        return {
            "date": self._today,
            "requests": 0,
            "consecutive_failures": 0,
            "circuit_open_until": 0.0,
            "last_request_at": 0.0,
            "retry_after_until": 0.0,
        }

    def _load_state(self) -> dict[str, Any]:
        state = self._default_state()
        if self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                raise EastmoneyStateCorrupt(
                    f"cannot read Eastmoney gate state: {self.state_path}"
                ) from exc
            if not isinstance(loaded, dict):
                raise EastmoneyStateCorrupt(
                    f"Eastmoney gate state must be an object: {self.state_path}"
                )
            required = {
                "date",
                "requests",
                "consecutive_failures",
                "circuit_open_until",
                "last_request_at",
            }
            missing = sorted(required.difference(loaded))
            if missing:
                raise EastmoneyStateCorrupt(
                    f"Eastmoney gate state missing fields: {','.join(missing)}"
                )
            state.update(loaded)

            if not isinstance(state["date"], str) or not state["date"]:
                raise EastmoneyStateCorrupt("Eastmoney gate state has invalid date")
            for key in (
                "requests",
                "consecutive_failures",
                "circuit_open_until",
                "last_request_at",
                "retry_after_until",
            ):
                try:
                    value = float(state[key])
                except (TypeError, ValueError) as exc:
                    raise EastmoneyStateCorrupt(
                        f"Eastmoney gate state has invalid {key}"
                    ) from exc
                if not math.isfinite(value) or value < 0:
                    raise EastmoneyStateCorrupt(
                        f"Eastmoney gate state has invalid {key}"
                    )

        if str(state.get("date")) != self._today:
            state["date"] = self._today
            state["requests"] = 0
            # A new day clears the request budget.  A circuit remains open
            # until its explicit cooldown has elapsed.
        for key, default in self._default_state().items():
            state.setdefault(key, default)
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.tmp"
        )
        temp_path.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temp_path, self.state_path)

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, Any]]:
        with _FileLock(
            self.lock_path,
            timeout=self.lock_timeout,
            poll_interval=self.lock_poll_interval,
            clock=self._clock,
            sleep=self._sleep,
        ):
            state = self._load_state()
            yield state

    def status(self) -> dict[str, Any]:
        """Return current bounded-state information without making a request."""

        with self._locked_state() as state:
            self._save_state(state)
            return dict(state)

    def _record_success(self) -> None:
        with self._locked_state() as state:
            state["consecutive_failures"] = 0
            state["circuit_open_until"] = 0.0
            self._save_state(state)

    def _record_failure(self) -> None:
        with self._locked_state() as state:
            failures = int(state.get("consecutive_failures") or 0) + 1
            state["consecutive_failures"] = failures
            if failures >= self.failure_threshold:
                state["circuit_open_until"] = self._clock() + self.cooldown_seconds
            self._save_state(state)

    def record_failure(self) -> None:
        """Record a semantic source failure after a valid HTTP response.

        For example, Eastmoney may return JSON successfully while omitting
        the requested ``klines`` list.  Such a response is not usable data and
        should participate in the same circuit breaker as transport errors.
        """

        self._record_failure()

    def record_success(self) -> None:
        """Reset consecutive failures after a caller validates a payload."""

        self._record_success()

    def _request_once(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        timeout: float | tuple[float, float] | None,
    ) -> dict[str, Any]:
        # Hold the same file lock from reservation through response parsing.
        # This prevents another process from slipping a request between the
        # interval check and the actual network call.
        with _FileLock(
            self.lock_path,
            timeout=self.lock_timeout,
            poll_interval=self.lock_poll_interval,
            clock=self._clock,
            sleep=self._sleep,
        ):
            state = self._load_state()
            now = self._clock()
            open_until = float(state.get("circuit_open_until") or 0.0)
            if open_until > now:
                raise EastmoneyCircuitOpen(
                    f"Eastmoney circuit open for {open_until - now:.0f}s"
                )
            retry_after_until = float(state.get("retry_after_until") or 0.0)
            if retry_after_until > now:
                raise EastmoneyRateLimited(
                    429,
                    retry_after_seconds=retry_after_until - now,
                    retry_after_until=retry_after_until,
                    failure_recorded=True,
                )
            requests_today = int(state.get("requests") or 0)
            if requests_today >= self.daily_budget:
                raise EastmoneyBudgetExceeded(
                    f"Eastmoney daily request budget exhausted: {self.daily_budget}"
                )

            wait = max(
                0.0,
                self.min_interval
                - (now - float(state.get("last_request_at") or 0.0)),
            )
            if self.jitter:
                wait += self.jitter * min(1.0, max(0.0, self._random()))
            if wait > 0:
                self._sleep(wait)

            state["requests"] = requests_today + 1
            state["last_request_at"] = self._clock()
            self._save_state(state)

            response = self._session.get(
                url,
                params=params or {},
                headers=headers,
                timeout=self.timeout if timeout is None else timeout,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code in (403, 429):
                retry_after_seconds = (
                    self._parse_retry_after(
                        getattr(response, "headers", None), now
                    )
                    if status_code == 429
                    else 0.0
                )
                retry_after_until = now + retry_after_seconds
                if retry_after_until > now:
                    state["retry_after_until"] = retry_after_until
                    self._save_state(state)
                raise EastmoneyRateLimited(
                    status_code,
                    retry_after_seconds=retry_after_seconds,
                    retry_after_until=retry_after_until,
                )
            response.raise_for_status()
            raw_text = str(getattr(response, "text", "") or "").strip()
            if not raw_text:
                raise EastmoneyEmptyResponse("Eastmoney returned an empty response")
            payload = response.json()
            if not isinstance(payload, dict) or not payload:
                raise EastmoneyEmptyResponse("Eastmoney returned an empty JSON payload")
            rc = payload.get("rc")
            if rc not in (0, None):
                raise EastmoneyRequestError(f"Eastmoney rc={rc}")
            return payload

    @staticmethod
    def _parse_retry_after(headers: Any, now: float) -> float:
        """Parse Retry-After seconds or an HTTP-date into a nonnegative wait."""

        if not headers:
            return 0.0
        raw = headers.get("Retry-After")
        if raw is None:
            return 0.0
        text = str(raw).strip()
        if not text:
            return 0.0
        try:
            return max(0.0, float(text))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(text)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - now)
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        """Perform one bounded Eastmoney GET with one Session rebuild retry."""

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                payload = self._request_once(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
            except EastmoneyRateLimited as exc:
                if not exc.failure_recorded:
                    self._record_failure()
                raise
            except (
                EastmoneyCircuitOpen,
                EastmoneyBudgetExceeded,
                EastmoneyStateCorrupt,
                EastmoneyLockUnavailable,
                EastmoneyLockTimeout,
            ):
                raise
            except Exception as exc:  # noqa: BLE001 - source errors are opaque.
                last_error = exc
                if attempt == 0:
                    self._reset_session()
                    continue
                self._record_failure()
                raise EastmoneyRequestError(
                    f"Eastmoney request failed after reconnect retry: {exc}"
                ) from exc
            else:
                self._record_success()
                return payload

        raise EastmoneyRequestError(f"Eastmoney request failed: {last_error}")

    request = get

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


__all__ = [
    "EastmoneyBudgetExceeded",
    "EastmoneyCircuitOpen",
    "EastmoneyEmptyResponse",
    "EastmoneyGateError",
    "EastmoneyLockTimeout",
    "EastmoneyLockUnavailable",
    "EastmoneyRateLimited",
    "EastmoneyRequestError",
    "EastmoneyRequestGate",
    "EastmoneyStateCorrupt",
]
