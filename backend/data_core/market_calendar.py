"""Offline A-share session guard for dates not yet present in price history."""

from datetime import date


# SSE's published 2026 closure schedule:
# https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml
_CLOSED_RANGES_2026 = tuple(
    (date.fromisoformat(start), date.fromisoformat(end))
    for start, end in (
        ("2026-01-01", "2026-01-04"),
        ("2026-02-14", "2026-02-23"),
        ("2026-02-28", "2026-02-28"),
        ("2026-04-04", "2026-04-06"),
        ("2026-05-01", "2026-05-05"),
        ("2026-05-09", "2026-05-09"),
        ("2026-06-19", "2026-06-21"),
        ("2026-09-20", "2026-09-20"),
        ("2026-09-25", "2026-09-27"),
        ("2026-10-01", "2026-10-07"),
        ("2026-10-10", "2026-10-10"),
    )
)


def is_a_share_session(day: date) -> bool:
    """Return whether an A-share daily download should expect market data."""
    if day.weekday() >= 5:
        return False
    if day.year == 2026:
        return not any(start <= day <= end for start, end in _CLOSED_RANGES_2026)
    return True
