"""Read-only source import. Run from backend: python -m research.import_reports."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

from research.repository import database_path, validate_date
from research.update_lock import writer_lock


def initialize(conn: sqlite3.Connection) -> None:
    """Create only the independent research schema, with bounded SQLite cache."""
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA cache_size=-4096')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS import_batches (
            id TEXT PRIMARY KEY, source_path TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL, scanned INTEGER DEFAULT 0,
            inserted INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, source_report_id TEXT NOT NULL,
            title TEXT NOT NULL, report_type TEXT NOT NULL, symbol TEXT NOT NULL,
            stock_name TEXT NOT NULL, industry_name TEXT NOT NULL, org_name TEXT NOT NULL,
            rating TEXT NOT NULL, researcher TEXT NOT NULL, publish_date TEXT NOT NULL,
            pdf_url TEXT NOT NULL, original_url TEXT NOT NULL, pages INTEGER NOT NULL,
            imported_at TEXT NOT NULL, raw_report_type TEXT NOT NULL,
            classification_reason TEXT NOT NULL, batch_id TEXT NOT NULL,
            UNIQUE(source, source_report_id));
        CREATE INDEX IF NOT EXISTS ix_reports_date ON reports(publish_date DESC, id DESC);
        CREATE INDEX IF NOT EXISTS ix_reports_symbol ON reports(symbol, publish_date DESC, id DESC);
        CREATE INDEX IF NOT EXISTS ix_reports_type ON reports(report_type, publish_date DESC, id DESC);
        CREATE INDEX IF NOT EXISTS ix_reports_industry ON reports(industry_name, publish_date DESC);
        CREATE INDEX IF NOT EXISTS ix_reports_org ON reports(org_name, publish_date DESC);
        CREATE INDEX IF NOT EXISTS ix_reports_batch ON reports(batch_id);
    ''')


def safe_url(value: str) -> str:
    """Preserve public HTTP(S) links only; never expose script/file URL schemes."""
    try:
        parsed = urlsplit(value)
        return value if (parsed.scheme in ('http', 'https') and parsed.hostname
                         and parsed.username is None and parsed.password is None) else ''
    except ValueError:
        return ''


def normalize(row: sqlite3.Row, batch_id: str, now: str) -> dict | None:
    """Classify by verified source semantics and retain original classification.

    Verified 2026-10-21: Eastmoney qType=0 is company, qType=1 is industry.
    The source crawler stores those labels reversed. Missing/invalid identifiers
    are not repaired by padding and do not acquire a speculative stock link.
    """
    data = dict(row)
    info_code = str(data.get('info_code') or '').strip()
    title = str(data.get('title') or '').strip()
    published = str(data.get('publish_date') or '')[:10]
    try:
        if not published:
            return None
        validate_date(published)
    except ValueError:
        return None
    if not re.fullmatch(r'AP[0-9]{8,40}', info_code) or not title:
        return None
    raw_type = str(data.get('report_type') or '')
    code = str(data.get('stock_code') or '').strip()
    industry = str(data.get('industry_name') or '').strip()
    # Explicit A-share exchange prefixes; overseas/fund/ambiguous codes stay unknown.
    valid_code = re.fullmatch(r'(?:00[0-9]|30[0-9]|60[0-9]|68[0-9]|43[0-9]|83[0-9]|87[0-9]|92[0-9])[0-9]{3}', code)
    if valid_code:
        report_type, symbol, reason = 'company', code, 'valid_a_share_code'
    elif not code and industry and raw_type == '个股研报':
        report_type, symbol, reason = 'industry', '', 'verified_source_qtype_1_industry'
    else:
        report_type, symbol, reason = 'unknown', '', 'insufficient_or_invalid_identity'
    try:
        pages = max(0, min(100000, int(data.get('pages') or 0)))
    except (TypeError, ValueError, OverflowError):
        pages = 0
    return {
        'id': f'eastmoney:{info_code}', 'source': 'eastmoney', 'source_report_id': info_code,
        'title': title, 'report_type': report_type, 'symbol': symbol,
        'stock_name': str(data.get('stock_name') or ''), 'industry_name': industry,
        'org_name': str(data.get('org_name') or ''), 'rating': str(data.get('rating') or ''),
        'researcher': str(data.get('researcher') or ''), 'publish_date': published,
        'pdf_url': safe_url(str(data.get('pdf_url') or '')),
        'original_url': (f'https://data.eastmoney.com/report/'
                         f"{'zw_industry' if report_type == 'industry' else 'zw_stock'}"
                         f'.jshtml?infocode={info_code}') if report_type != 'unknown' else '',
        'pages': pages, 'imported_at': now, 'raw_report_type': raw_type,
        'classification_reason': reason, 'batch_id': batch_id,
    }


def import_reports(source: str | Path, target: str | Path | None = None,
                   batch_size: int = 500) -> dict:
    """Serialize source imports with scheduled updates using the shared writer lock."""
    path = Path(target) if target is not None else database_path()
    source_path = Path(source).resolve()
    if source_path == path.resolve():
        raise ValueError('source and target must differ')
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000')
    with writer_lock(path):
        return _import_reports_unlocked(source_path, path, batch_size)


def _import_reports_unlocked(source: str | Path, target: str | Path | None = None,
                             batch_size: int = 500) -> dict:
    """Import in bounded transactions, skipping existing IDs; audit partial failure."""
    if not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000')
    source = Path(source).resolve()
    target = Path(target) if target is not None else database_path()
    if source == target.resolve():
        raise ValueError('source and target must differ')
    if not source.is_file():
        raise FileNotFoundError(source)
    batch_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        src.row_factory = sqlite3.Row
        src.execute('PRAGMA query_only=ON')
        src.execute('PRAGMA cache_size=-4096')
        rows = src.execute('SELECT * FROM reports ORDER BY info_code')
        required = {'info_code', 'title', 'report_type', 'stock_code', 'publish_date'}
        if not required.issubset({column[0] for column in rows.description}):
            raise ValueError('unsupported source schema')
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target, timeout=10)) as dst:
            initialize(dst)
            with dst:
                dst.execute('INSERT INTO import_batches(id,source_path,started_at,status) '
                            'VALUES (?,?,?,?)', (batch_id, str(source), now, 'RUNNING'))
            scanned = inserted = skipped = 0
            try:
                while batch := rows.fetchmany(batch_size):
                    chunk_inserted = chunk_skipped = 0
                    with dst:
                        for row in batch:
                            record = normalize(row, batch_id, now)
                            if record is None:
                                chunk_skipped += 1
                                continue
                            columns = ','.join(record)
                            placeholders = ','.join('?' for _ in record)
                            count = dst.execute(
                                f'INSERT OR IGNORE INTO reports ({columns}) VALUES ({placeholders})',
                                list(record.values())).rowcount
                            chunk_inserted += count
                            chunk_skipped += 1 - count
                        dst.execute('UPDATE import_batches SET scanned=?, inserted=?, skipped=? '
                                    'WHERE id=?', (scanned + len(batch), inserted + chunk_inserted,
                                                  skipped + chunk_skipped, batch_id))
                    scanned += len(batch)
                    inserted += chunk_inserted
                    skipped += chunk_skipped
            except Exception:
                with dst:
                    dst.execute("UPDATE import_batches SET status='FAILED', finished_at=? "
                                'WHERE id=?', (datetime.now(timezone.utc).isoformat(), batch_id))
                raise
            with dst:
                dst.execute("UPDATE import_batches SET status='COMPLETED', finished_at=? "
                            'WHERE id=?', (datetime.now(timezone.utc).isoformat(), batch_id))
            dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return {'batch_id': batch_id, 'scanned': scanned, 'inserted': inserted, 'skipped': skipped}


def rollback_batch(batch_id: str, target: str | Path | None = None) -> int:
    """Serialize rollback with imports and scheduled collection."""
    path = Path(target) if target is not None else database_path()
    with writer_lock(path):
        return _rollback_batch_unlocked(batch_id, path)


def _rollback_batch_unlocked(batch_id: str, target: str | Path | None = None) -> int:
    """Atomically delete only rows first introduced by the specified import batch."""
    path = Path(target) if target is not None else database_path()
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True,
                                 timeout=10)) as conn:
        with conn:
            row = conn.execute('SELECT status FROM import_batches WHERE id=?',
                               (batch_id,)).fetchone()
            if not row:
                raise ValueError('unknown import batch')
            if row[0] == 'RUNNING':
                raise ValueError('cannot rollback a running import')
            count = conn.execute('DELETE FROM reports WHERE batch_id=?', (batch_id,)).rowcount
            conn.execute("UPDATE import_batches SET status='ROLLED_BACK' WHERE id=?", (batch_id,))
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return count


def main() -> None:
    """Import or explicitly roll back one audited batch."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--source')
    action.add_argument('--rollback-batch')
    parser.add_argument('--target')
    parser.add_argument('--batch-size', type=int, default=500)
    args = parser.parse_args()
    if args.rollback_batch:
        result = {'deleted': rollback_batch(args.rollback_batch, args.target)}
    else:
        result = import_reports(args.source, args.target, args.batch_size)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
