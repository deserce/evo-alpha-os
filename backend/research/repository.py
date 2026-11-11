"""Bounded, read-only queries for the independent research catalogue."""
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import re
import sqlite3
import time


def database_path() -> Path:
    return Path(os.environ.get('EVOALPHA_RESEARCH_DB') or
                Path(__file__).resolve().parents[1] / 'data' / 'research.db')


def validate_date(value: str) -> str:
    if value and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)
                  or date.fromisoformat(value).isoformat() != value):
        raise ValueError('日期必须为 YYYY-MM-DD')
    return value


class Repository:
    """Open a short-lived read-only connection per request; never initialize on GET."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else database_path()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro',
                                     uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA cache_size=-4096')
        deadline = time.monotonic() + 3.0
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        return connection

    def list_reports(self, q: str = '', symbol: str = '', report_type: str = '',
                     industry: str = '', org: str = '', date_from: str = '',
                     date_to: str = '', page: int = 1, page_size: int = 30) -> dict:
        """Return one stable page; search text is literal, including SQL wildcards."""
        q = q.strip()
        if len(q) > 120 or len(industry) > 120 or len(org) > 120:
            raise ValueError('筛选文本最长 120 字')
        if symbol and not re.fullmatch(r'[0-9]{6}', symbol):
            raise ValueError('股票代码必须为六位数字')
        if report_type not in ('', 'company', 'industry', 'unknown'):
            raise ValueError('无效研报类型')
        if (type(page) is not int or type(page_size) is not int
                or not 1 <= page <= 100000 or not 1 <= page_size <= 100):
            raise ValueError('页码或每页条数越界')
        validate_date(date_from)
        validate_date(date_to)
        if date_from and date_to and date_from > date_to:
            raise ValueError('开始日期不得晚于结束日期')
        result = {'items': [], 'total': 0, 'page': page, 'page_size': page_size,
                  'status': 'ready' if self.db_path.exists() else 'not_imported'}
        if not self.db_path.exists():
            return result
        clauses, params = [], []
        for column, value in (('symbol', symbol), ('report_type', report_type),
                              ('industry_name', industry), ('org_name', org)):
            if value:
                clauses.append(f'{column} = ?')
                params.append(value)
        for operator, value in (('>=', date_from), ('<=', date_to)):
            if value:
                clauses.append(f'publish_date {operator} ?')
                params.append(value)
        if q:
            escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            clauses.append("(title LIKE ? ESCAPE '\\' OR stock_name LIKE ? ESCAPE '\\' "
                           "OR symbol = ?)")
            params.extend([f'%{escaped}%', f'%{escaped}%', q])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with closing(self._connect()) as conn:
            conn.execute('BEGIN')
            result['total'] = conn.execute('SELECT count(*) FROM reports' + where,
                                           params).fetchone()[0]
            rows = conn.execute('SELECT * FROM reports' + where +
                                ' ORDER BY publish_date DESC, id DESC LIMIT ? OFFSET ?',
                                [*params, page_size, (page - 1) * page_size])
            result['items'] = [dict(row) for row in rows]
        return result

    def get_report(self, report_id: str) -> dict | None:
        """Return a report by its stable source-qualified ID."""
        if not report_id or len(report_id) > 160:
            raise ValueError('无效研报 ID')
        if not self.db_path.exists():
            return None
        with closing(self._connect()) as conn:
            row = conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
            return dict(row) if row else None

    def facets(self) -> dict:
        """Return bounded filter options and catalogue date coverage."""
        result = {'industries': [], 'organizations': [], 'report_types': [], 'total': 0,
                  'date_from': None, 'date_to': None,
                  'status': 'ready' if self.db_path.exists() else 'not_imported',
                  'sync': self._empty_sync_status()}
        if not self.db_path.exists():
            return result
        with closing(self._connect()) as conn:
            conn.execute('BEGIN')
            for key, column in (('industries', 'industry_name'), ('organizations', 'org_name'),
                                ('report_types', 'report_type')):
                result[key] = [row[0] for row in conn.execute(
                    f"SELECT DISTINCT {column} FROM reports WHERE {column} != '' "
                    f'ORDER BY {column} LIMIT 500')]
            row = conn.execute('SELECT count(*), min(publish_date), max(publish_date) '
                               'FROM reports').fetchone()
            result.update(total=row[0], date_from=row[1], date_to=row[2])
            result['sync'] = self._sync_status(conn)
        return result

    @staticmethod
    def _empty_sync_status() -> dict:
        return {'status': 'never', 'last_attempt_at': None, 'last_success_at': None,
                'coverage_through': None, 'inserted': 0, 'updated': 0, 'error': None,
                'schedule': '每天 09:00（北京时间）'}

    def _sync_status(self, conn: sqlite3.Connection) -> dict:
        """Read deterministic update evidence; publication dates never imply success."""
        result = self._empty_sync_status()
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('sync_state', 'sync_runs')")}
        if tables != {'sync_state', 'sync_runs'}:
            return result
        state = conn.execute(
            'SELECT cursor_date, last_success FROM sync_state WHERE source=?',
            ('eastmoney',)).fetchone()
        if state:
            result.update(coverage_through=state['cursor_date'],
                          last_success_at=state['last_success'])
        latest = conn.execute(
            'SELECT status, started_at, inserted, updated FROM sync_runs '
            'ORDER BY started_at DESC, id DESC LIMIT 1').fetchone()
        if latest:
            status = str(latest['status']).lower()
            result.update(status=status if status in (
                'running', 'success', 'failed', 'partial') else 'failed',
                last_attempt_at=latest['started_at'], inserted=latest['inserted'],
                updated=latest['updated'])
            if result['status'] in ('failed', 'partial'):
                result['error'] = '最近一次更新未完整完成，已保留进度供后续补抓。'
        return result
