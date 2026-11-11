"""Isolated catalogue tests; no production data or network."""
from contextlib import closing
import sqlite3
import pytest
from research.import_reports import import_reports, rollback_batch, safe_url
from research.repository import Repository
from research.update_lock import UpdateLocked, writer_lock


@pytest.fixture
def catalog(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'research.db'
    with closing(sqlite3.connect(source)) as conn:
        conn.execute('''CREATE TABLE reports(info_code TEXT, title TEXT, report_type TEXT,
                     stock_code TEXT, stock_name TEXT, industry_name TEXT, org_name TEXT,
                     publish_date TEXT, pdf_url TEXT, pages INTEGER)''')
        conn.executemany('INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?)', [
            ('AP202610210001', '公司业绩 100%', '行业研报', '000001', '平安银行',
             '银行', '机构甲', '2026-10-21', 'https://example.com/a.pdf', 10),
            ('AP202610200002', '行业观察', '个股研报', '', '', '银行', '机构乙',
             '2026-10-20', 'javascript:alert(1)', 2),
            ('AP202610190003', '异常股票代码', '行业研报', '123', '', '', '机构甲',
             '2026-10-19', '', 0),
            ('AP202610180004', '分类不明', '个股研报', '', '', '', '机构甲',
             '2026-10-18', '', 0),
            ('AP202610170005', '坏日期', '行业研报', '600519', '', '', '',
             'not-a-date', '', 0),
        ])
        conn.commit()
    return source, target


def test_import_classification_idempotency_and_source_unchanged(catalog):
    source, target = catalog
    original = source.read_bytes()
    first = import_reports(source, target, batch_size=2)
    assert (first['scanned'], first['inserted'], first['skipped']) == (5, 4, 1)
    repo = Repository(target)
    items = repo.list_reports()['items']
    assert [item['report_type'] for item in items] == ['company', 'industry', 'unknown', 'unknown']
    assert items[0]['symbol'] == '000001'
    assert items[0]['raw_report_type'] == '行业研报'
    assert items[1]['pdf_url'] == ''
    assert repo.get_report(items[0]['id']) == items[0]
    second = import_reports(source, target)
    assert second['inserted'] == 0
    assert source.read_bytes() == original
    assert rollback_batch(second['batch_id'], target) == 0
    assert repo.list_reports()['total'] == 4
    assert rollback_batch(first['batch_id'], target) == 4
    assert repo.list_reports()['total'] == 0


def test_pagination_literal_search_exact_symbol_and_date(catalog):
    source, target = catalog
    import_reports(source, target)
    repo = Repository(target)
    assert repo.list_reports(page=2, page_size=1)['items'][0]['title'] == '行业观察'
    assert repo.list_reports(symbol='000001')['total'] == 1
    assert repo.list_reports(q='%')['total'] == 1
    assert repo.list_reports(q="' OR 1=1 --")['total'] == 0
    assert repo.list_reports(date_to='2026-10-20')['total'] == 3
    assert repo.list_reports(date_from='2026-10-20', date_to='2026-10-20')['total'] == 1
    assert repo.list_reports(industry='银行', org='机构甲', report_type='company')['total'] == 1
    facets = repo.facets()
    assert set(facets['organizations']) == {'机构乙', '机构甲'}
    assert facets['date_to'] == '2026-10-21'


def test_reads_do_not_create_database(tmp_path, monkeypatch):
    path = tmp_path / 'absent' / 'research.db'
    monkeypatch.setenv('EVOALPHA_RESEARCH_DB', str(path))
    repo = Repository()
    assert repo.list_reports()['status'] == 'not_imported'
    assert repo.facets()['total'] == 0
    assert repo.get_report('eastmoney:AP202610210001') is None
    assert not path.parent.exists()


@pytest.mark.parametrize('arguments', [
    {'page': 0}, {'page_size': 101}, {'symbol': '1'}, {'symbol': '１２３４５６'},
    {'report_type': 'other'}, {'date_to': '2026-02-30'},
    {'date_from': '2026-10-21', 'date_to': '2026-10-20'}, {'q': 'x' * 121},
])
def test_invalid_inputs_rejected_before_read(tmp_path, arguments):
    with pytest.raises(ValueError):
        Repository(tmp_path / 'missing.db').list_reports(**arguments)


def test_source_and_target_errors_are_explicit(catalog, tmp_path):
    source, target = catalog
    with pytest.raises(ValueError):
        import_reports(source, source)
    with pytest.raises(FileNotFoundError):
        import_reports(tmp_path / 'missing', target)
    with pytest.raises(ValueError):
        import_reports(source, target, batch_size=1001)
    import_reports(source, target)
    with pytest.raises(ValueError):
        rollback_batch('nonexistent', target)
    broken = tmp_path / 'broken.db'
    broken.write_bytes(b'not sqlite')
    with pytest.raises(sqlite3.DatabaseError):
        Repository(broken).list_reports()


def test_unsupported_source_schema_does_not_create_target(tmp_path):
    source, target = tmp_path / 'source.db', tmp_path / 'research.db'
    with closing(sqlite3.connect(source)) as conn:
        conn.execute('CREATE TABLE reports (info_code TEXT)')
    with pytest.raises(ValueError, match='unsupported source schema'):
        import_reports(source, target)
    assert not target.exists()


@pytest.mark.parametrize('url', [
    'https://user:secret@example.com/report.pdf',
    'https://user@example.com/report.pdf',
    'https://@example.com/report.pdf',
    'https://:secret@example.com/report.pdf',
])
def test_url_credentials_are_not_exposed(url):
    assert safe_url(url) == ''


def test_unknown_report_does_not_guess_original_route(catalog):
    source, target = catalog
    import_reports(source, target)
    items = Repository(target).list_reports(report_type='unknown')['items']
    assert len(items) == 2
    assert all(item['original_url'] == '' for item in items)


def test_import_and_rollback_share_update_writer_lock(catalog):
    source, target = catalog
    with writer_lock(target):
        with pytest.raises(UpdateLocked):
            import_reports(source, target)
    imported = import_reports(source, target)
    with writer_lock(target):
        with pytest.raises(UpdateLocked):
            rollback_batch(imported['batch_id'], target)
    assert Repository(target).list_reports()['total'] == 4
