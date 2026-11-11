"""Catalogue update status stays distinct from report publication dates."""
from contextlib import closing
import sqlite3

from research.repository import Repository


def make_catalogue(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('''
            CREATE TABLE reports (industry_name TEXT, org_name TEXT, report_type TEXT,
                                  publish_date TEXT);
            INSERT INTO reports VALUES ('银行', '机构甲', 'company', '2026-10-21');
            CREATE TABLE sync_state (source TEXT PRIMARY KEY, cursor_date TEXT,
                                     last_success TEXT, last_scheduled_date TEXT);
            CREATE TABLE sync_runs (id TEXT PRIMARY KEY, status TEXT, started_at TEXT,
                                    finished_at TEXT, inserted INTEGER, updated INTEGER,
                                    error TEXT);
            INSERT INTO sync_state VALUES ('eastmoney', '2026-10-24',
                '2026-10-24T01:02:00+00:00', '2026-10-24');
            INSERT INTO sync_runs VALUES ('old', 'SUCCESS', '2026-10-24T01:00:00+00:00',
                '2026-10-24T01:02:00+00:00', 5, 2, '');
        ''')
        conn.commit()


def test_imported_catalogue_does_not_claim_a_successful_sync(tmp_path):
    path = tmp_path / 'catalogue.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE reports (industry_name TEXT, org_name TEXT, '
                     'report_type TEXT, publish_date TEXT)')
    result = Repository(path).facets()['sync']
    assert result['status'] == 'never'
    assert result['last_success_at'] is None
    assert Repository(tmp_path / 'absent.db').facets()['sync']['status'] == 'never'


def test_successful_zero_new_reports_still_exposes_coverage(tmp_path):
    path = tmp_path / 'catalogue.db'
    make_catalogue(path)
    result = Repository(path).facets()
    assert result['date_to'] == '2026-10-21'
    assert result['sync']['coverage_through'] == '2026-10-24'
    assert result['sync']['last_success_at'] == '2026-10-24T01:02:00+00:00'
    assert result['sync']['status'] == 'success'
    assert result['sync']['inserted'] == 5


def test_latest_failed_run_does_not_hide_previous_success_or_expose_error(tmp_path):
    path = tmp_path / 'catalogue.db'
    make_catalogue(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('INSERT INTO sync_runs VALUES (?,?,?,?,?,?,?)', (
            'new', 'FAILED', '2026-10-25T01:00:00+00:00',
            '2026-10-25T01:01:00+00:00', 0, 0, '/private/path?secret=hidden'))
        conn.commit()
    result = Repository(path).facets()['sync']
    assert result['status'] == 'failed'
    assert result['coverage_through'] == '2026-10-24'
    assert result['last_success_at'] == '2026-10-24T01:02:00+00:00'
    assert 'secret' not in result['error']
    assert result['last_attempt_at'] == '2026-10-25T01:00:00+00:00'
