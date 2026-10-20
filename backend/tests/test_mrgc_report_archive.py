"""Archive reuse, source invalidation and bounded atomic storage."""
from contextlib import closing
import sqlite3
import pytest
from tests.test_structure_monitor import make_db, _buy_day


def report_for(prices):
    return {'date': _buy_day(prices), 'status': 'READY', 'ai': {'status': 'READY'},
            'groups': [{'id': 'watchlist', 'symbols': ['600641']}], 'stocks': []}


def test_reuse_source_repair_future_and_membership(tmp_path):
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    sig = archive.source_signature(path, report)
    archive.write_archive(path, report, sig)
    assert archive.read_archive(path, report['date'])['valid']
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('DELETE FROM stock_pool_members')
        conn.execute('UPDATE stock_daily_prices SET close=close+1 WHERE trade_date>?', (report['date'],))
    assert archive.read_archive(path, report['date'])['valid']
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('UPDATE stock_daily_prices SET close=close+1 WHERE trade_date=?', (report['date'],))
    assert not archive.read_archive(path, report['date'])['valid']


def test_partial_does_not_replace_complete(tmp_path):
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    sig = archive.source_signature(path, report)
    archive.write_archive(path, report, sig)
    report['ai']['status'] = 'FAILED'
    archive.write_archive(path, report, sig)
    assert archive.read_archive(path, report['date'])['report']['ai']['status'] == 'READY'
    assert archive.read_partial(path, report['date'])['report']['ai']['status'] == 'FAILED'


def test_dates_and_budget(tmp_path, monkeypatch):
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    with pytest.raises(ValueError):
        archive.read_archive(path, '../secret')
    report = report_for(prices)
    sig = archive.source_signature(path, report)
    monkeypatch.setattr(archive, 'MAX_TOTAL_BYTES', 1)
    with pytest.raises(ValueError):
        archive.write_archive(path, report, sig)
    assert archive.read_archive(path, report['date']) is None


def test_rules_change_and_bounded_revision_cleanup(tmp_path, monkeypatch):
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    sig = archive.source_signature(path, report)
    archive.write_archive(path, report, sig)
    monkeypatch.setattr(archive, 'PROMPT_VERSION', 'new-prompt')
    assert not archive.read_archive(path, report['date'])['valid']
    archive.write_archive(path, report, archive.source_signature(path, report))
    root = archive.directory(path)
    revisions = list(root.glob(report['date'] + '-*.json'))
    assert len(revisions) == 1
    latest = root / (report['date'] + '.json')
    monkeypatch.setattr(archive, 'MAX_TOTAL_BYTES', 2 * latest.stat().st_size + 50)
    # A new revision preserves its predecessor and reclaims older revisions.
    archive.write_archive(path, report, archive.source_signature(path, report))
    assert archive.read_archive(path, report['date'])['valid']
    assert latest.exists()


def test_oversize_and_untrusted_archive_id(tmp_path):
    import json
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    sig = archive.source_signature(path, report)
    archive.write_archive(path, report, sig)
    location = archive.directory(path) / (report['date'] + '.json')
    envelope = json.loads(location.read_text())
    envelope['archive_id'] = '../../elsewhere'
    location.write_text(json.dumps(envelope))
    assert archive.read_archive(path, report['date']) is None
    report['large'] = 'x' * archive.MAX_FILE_BYTES
    with pytest.raises(ValueError):
        archive.write_archive(path, report, sig)


def test_same_inputs_regeneration_preserves_previous(tmp_path):
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    sig = archive.source_signature(path, report)
    first = archive.write_archive(path, report, sig)
    second = archive.write_archive(path, report, sig)
    assert first['archive_id'] != second['archive_id']
    assert (archive.directory(path) / (first['archive_id'] + '.json')).exists()


def test_validation_cache_and_database_stat_invalidation(tmp_path, monkeypatch):
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    sig = archive.source_signature(path, report)
    archive.write_archive(path, report, sig)
    calls = []
    real = archive.source_signature
    def counted(db_path, value):
        calls.append(1)
        return real(db_path, value)
    monkeypatch.setattr(archive, 'source_signature', counted)
    assert archive.read_archive(path, report['date'])['valid']
    assert archive.read_archive(path, report['date'])['valid']
    assert len(calls) == 1
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('UPDATE stock_daily_prices SET close=close+1 WHERE trade_date=?', (report['date'],))
    assert not archive.read_archive(path, report['date'])['valid']
    assert len(calls) == 2
    path.unlink()
    stale = archive.read_archive(path, report['date'])
    assert stale['stale_reason'] == 'source_validation_unavailable'
    assert stale['report'] == report


def test_published_rules_are_complete_but_missing_facts_are_not(tmp_path):
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    report['source'] = {'publication_mode': 'rules'}
    report['ai'] = {'status': 'UNCONFIGURED'}
    sig = archive.source_signature(path, report)
    archive.write_archive(path, report, sig)
    assert archive.read_archive(path, report['date'])['valid']
    report['missing'] = ['missing member']
    assert not archive.is_complete(report)
    report['missing'] = []
    report['source'] = {}
    report['ai'] = {'status': 'FAILED'}
    assert not archive.is_complete(report)


@pytest.mark.parametrize('broken', [[], {'report': None}, {'report': {'date': '2026-02-12', 'source': []}}])
def test_corrupt_archive_degrades_to_missing_without_500(tmp_path, broken):
    import json
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    root = archive.directory(path)
    root.mkdir()
    (root / (report['date'] + '.json')).write_text(json.dumps(broken))
    assert archive.read_archive(path, report['date']) is None


@pytest.mark.parametrize('field', ['source', 'ai', 'groups'])
def test_valid_envelope_with_corrupt_nested_types_is_rejected(tmp_path, field):
    import json
    from quant_engine.watch import mrgc_report_archive as archive
    path, prices = make_db(tmp_path)
    report = report_for(prices)
    archive.write_archive(path, report, archive.source_signature(path, report))
    file = archive.directory(path) / (report['date'] + '.json')
    envelope = json.loads(file.read_text())
    envelope['report'][field] = None if field == 'groups' else []
    file.write_text(json.dumps(envelope))
    assert archive.read_archive(path, report['date']) is None
