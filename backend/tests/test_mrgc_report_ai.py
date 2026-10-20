"""Provider isolation and cache regression tests."""
from unittest.mock import Mock
import httpx
import pytest
from quant_engine.watch import mrgc_report_ai as ai

@pytest.fixture
def report():
    return {"date": "2026-09-30", "status": "READY", "missing": [],
            "counts": {"total": 1}, "summary": ["规则摘要"],
            "sectors": [{"name": "半导体", "count": 1}],
            "stocks": [{"symbol": "600001", "price": 20, "events": []}],
            "source": {"version": "v2"}}


def test_success_cache_force_no_repeat(monkeypatch, tmp_path, report):
    monkeypatch.setattr(ai.settings, "GLM_API_KEY", "test-key")
    provider = Mock(return_value={"summary": ["今日仅观察"],
                    "sector_notes": {"半导体": "集中度较高"},
                    "stock_notes": {"600001": "模拟持有"}})
    monkeypatch.setattr(ai, "_provider", provider)
    result = ai.enrich_report(report, tmp_path)
    assert result["ai"]["status"] == "READY"
    assert "ai" not in report
    assert result["stocks"] == report["stocks"]
    assert ai.enrich_report(report, tmp_path, force=True) == result
    assert provider.call_count == 1
    assert ai.read_cached(report, tmp_path) == result


def test_unconfigured(monkeypatch, tmp_path, report):
    monkeypatch.setattr(ai.settings, "GLM_API_KEY", "")
    provider = Mock()
    monkeypatch.setattr(ai, "_provider", provider)
    assert ai.enrich_report(report, tmp_path)["ai"]["status"] == "UNCONFIGURED"
    provider.assert_not_called()


def test_blocked(monkeypatch, tmp_path, report):
    provider = Mock()
    monkeypatch.setattr(ai, "_provider", provider)
    report["missing"] = ["行情缺失"]
    assert ai.enrich_report(report, tmp_path)["ai"]["status"] == "BLOCKED"
    provider.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_timeout_no_retry(monkeypatch, tmp_path, report):
    monkeypatch.setattr(ai.settings, "GLM_API_KEY", "test-key")
    provider = Mock(side_effect=httpx.ReadTimeout("timeout"))
    monkeypatch.setattr(ai, "_provider", provider)
    assert ai.enrich_report(report, tmp_path)["ai"]["status"] == "FAILED"
    ai.enrich_report(report, tmp_path)
    assert provider.call_count == 1


def test_invalid_reference(monkeypatch, tmp_path, report):
    monkeypatch.setattr(ai.settings, "GLM_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_provider", Mock(return_value={"summary": ["摘要"],
                       "stock_notes": {"999999": "新增买点"}}))
    result = ai.enrich_report(report, tmp_path)
    assert result["ai"]["status"] == "FAILED"
    assert result["stocks"] == report["stocks"]


def test_source_change_read_only(monkeypatch, tmp_path, report):
    monkeypatch.setattr(ai.settings, "GLM_API_KEY", "")
    assert ai.read_cached(report, tmp_path) is None
    assert list(tmp_path.iterdir()) == []
    ai.enrich_report(report, tmp_path)
    report["stocks"][0]["price"] = 21
    assert ai.read_cached(report, tmp_path) is None


def test_retention(tmp_path, report):
    import os
    old = tmp_path / "old.json"
    old.write_text("{}")
    os.utime(old, (0, 0))
    ai._save(report, tmp_path)
    assert not old.exists()


def test_stream_budget_and_safe_endpoint(monkeypatch, report):
    monkeypatch.setattr(ai.settings, "GLM_API_URL", "https://provider.example/v4/")
    client_class = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (129 * 1024)))
    monkeypatch.setattr(ai.httpx, "Client", lambda **kwargs: client_class(transport=transport, **kwargs))
    with pytest.raises(ValueError, match="response exceeds budget"):
        ai._provider(report)
    monkeypatch.setattr(ai.settings, "GLM_API_URL", "http://untrusted.example/v4/")
    with pytest.raises(ValueError, match="unsupported"):
        ai._provider(report)


def test_invalid_cache_status_rejected(tmp_path, report):
    import json
    cached = {**report, "ai": {"status": "BOGUS", "model": "test", "reason": ""}}
    (tmp_path / f"{ai._key(report)}.json").write_text(json.dumps(cached))
    assert ai.read_cached(report, tmp_path) is None


def test_concurrent_lock_is_nonblocking(monkeypatch, tmp_path, report):
    import fcntl
    provider = Mock()
    monkeypatch.setattr(ai, "_provider", provider)
    with (tmp_path / ".generation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert ai.enrich_report(report, tmp_path)["ai"]["status"] == "BUSY"
    provider.assert_not_called()


def test_prompt_includes_group_overlap_chan_provenance_and_bounds_individuals(report):
    import json
    report['groups'] = [dict(id='company', name='公司', membership_as_of='current',
                            count=25, status='READY', opportunity_counts={'buy': 1})]
    report['stocks'] = [dict(symbol=f'{index:06d}', data_status='OK', group_ids=['company'],
        group_names=['公司'], overlap_count=1, conflict=False, events=[],
        opportunities=[dict(direction='buy', level='resonance', source='C_CHAN',
            label='C确认＋缠论候选共振', confirmed_date=report['date'], evidence=['真实依据'])],
        chan_signals=[dict(direction='buy', label='候选', pivot_date='2026-09-25',
            confirmation_date=report['date'], candidate=True, repainting=True)]) for index in range(25)]
    prompt = json.loads(ai._prompt(report))
    assert len(prompt['stocks']) == 20
    assert prompt['groups'][0]['membership_as_of'] == 'current'
    assert prompt['stocks'][0]['opportunities'][0]['level'] == 'resonance'
    assert prompt['stocks'][0]['chan_signals'][0]['repainting'] is True
    assert prompt['stocks'][0]['chan_signals'][0]['pivot_date'] == '2026-09-25'


def test_prompt_excludes_untrusted_individual_and_hash_changes_with_group_or_chan(report):
    import copy, json
    report['stocks'][0]['data_status'] = 'BLOCKED'
    assert json.loads(ai._prompt(report))['stocks'] == []
    original = ai._key(report)
    changed = copy.deepcopy(report)
    changed['groups'] = [{'id': 'company', 'count': 3}]
    assert ai._key(changed) != original
    changed['stocks'][0]['chan_signals'] = [{'confirmation_date': '2026-09-30'}]
    assert ai._key(changed) != ai._key({**changed, 'stocks': report['stocks']})


def test_prompt_rejects_oversized_input_before_any_provider_call(report):
    report['source']['rules'] = ['x' * 21000]
    with pytest.raises(ValueError, match='prompt exceeds budget'):
        ai._prompt(report)


def test_prompt_reduces_individuals_to_budget_and_rejects_unprovided_note(report):
    import json
    report['stocks'] = [dict(symbol=f'{index:06d}', name='长名称' * 20, events=[],
        opportunities=[dict(direction='buy', level='resonance', label='共振候选', source='C_CHAN',
            evidence=['证据' * 70, '另一证据' * 70]) for _ in range(4)]) for index in range(25)]
    content = ai._prompt(report)
    facts = json.loads(content)
    assert len(content) <= 20000 and 0 < len(facts['stocks']) < 20
    assert facts['individuals_included'] == len(facts['stocks'])
    with pytest.raises(ValueError, match='invalid note text or reference'):
        ai._validate({'summary': ['总结'], 'stock_notes': {'000024': '未提供的个股分析'}}, report)

