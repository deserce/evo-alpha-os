"""Archive endpoints delegate date validation and never call strategies inline."""
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app.api.v1 import watch
from quant_engine.watch import mrgc_report_jobs as jobs


def test_get_reads_saved_report_without_calculating(monkeypatch):
    payload = {'report': {'date': '2026-09-29'}, 'job': {'status': 'COMPLETED'}}
    reader = Mock(return_value=payload)
    monkeypatch.setattr(jobs, 'get_report', reader)
    monkeypatch.setattr(jobs, 'build_report', Mock(side_effect=AssertionError('no calculation')))
    assert watch.get_mrgc_daily_report('2026-09-29').data == payload
    reader.assert_called_once_with(watch.WATCH_DB_PATH, '2026-09-29')


def test_post_starts_coalesced_task_with_explicit_regeneration(monkeypatch):
    starter = Mock(return_value={'report': None, 'job': {'status': 'RUNNING'}})
    monkeypatch.setattr(jobs, 'start_generation', starter)
    result = watch.generate_mrgc_daily_report(watch.MrgcReportGenerateRequest(
        trade_date='2026-09-29', regenerate=True))
    assert result.data['job']['status'] == 'RUNNING'
    starter.assert_called_once_with(watch.WATCH_DB_PATH, '2026-09-29', True)


@pytest.mark.parametrize('method', ['get', 'post'])
def test_invalid_date_returns_400(monkeypatch, method):
    monkeypatch.setattr(jobs, 'get_report', Mock(side_effect=ValueError('invalid date')))
    monkeypatch.setattr(jobs, 'start_generation', Mock(side_effect=ValueError('invalid date')))
    with pytest.raises(HTTPException) as error:
        if method == 'get':
            watch.get_mrgc_daily_report('2026-99-99')
        else:
            watch.generate_mrgc_daily_report(watch.MrgcReportGenerateRequest(trade_date='2026-99-99'))
    assert error.value.status_code == 400


def test_archive_read_failure_returns_503(monkeypatch):
    monkeypatch.setattr(jobs, 'get_report', Mock(side_effect=OSError('disk unavailable')))
    with pytest.raises(HTTPException) as error:
        watch.get_mrgc_daily_report('2026-09-29')
    assert error.value.status_code == 503
