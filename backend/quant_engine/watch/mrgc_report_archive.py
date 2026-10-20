"""Immutable dated AI reports with source validation and bounded local storage."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from app.core.config import settings
from quant_engine.research.structure_turning import VERSION
from quant_engine.watch.mrgc_daily_report import REPORT_VERSION
from quant_engine.watch.smart_mrgc_pool import resolve_members
from quant_engine.watch.structure_monitor import StructureMonitor, _day

ARCHIVE_VERSION = 'dated_ai_report_v1'
PROMPT_VERSION = 'mrgc-archive-v1'
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024


def directory(db_path: Path | str) -> Path:
    return Path(db_path).parent / 'mrgc_report_archives'


def _encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def source_signature(db_path: Path | str, report: dict) -> str:
    """Stream historical inputs into a digest; future data and current pools are excluded."""
    target = _day(report['date'])
    groups = [{'id': group['id'], 'symbols': sorted(group.get('symbols', []))}
              for group in report.get('groups', [])]
    symbols = sorted({symbol for group in groups for symbol in group['symbols']})
    if len(symbols) > 300 or any(not re.fullmatch(r'[0-9]{6}', str(s)) for s in symbols):
        raise ValueError('invalid archived membership')
    digest = hashlib.sha256()
    def add(value: object) -> None:
        digest.update(_encode(value) + b'\n')
    add([ARCHIVE_VERSION, REPORT_VERSION, VERSION, StructureMonitor(db_path).config_hash,
         settings.GLM_MODEL, getattr(settings, 'GLM_API_URL', ''), PROMPT_VERSION, target, groups])
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro',
                                 uri=True, timeout=2)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        add(resolve_members(conn, target))
        for symbol in symbols:
            add(symbol)
            if 'stock_daily_prices' in tables:
                query = ('SELECT substr(trade_date,1,10),open,high,low,close,volume '
                         'FROM stock_daily_prices WHERE symbol=? AND trade_date>=? '
                         'AND trade_date<? ORDER BY trade_date LIMIT 1501')
                for row in conn.execute(query, (symbol, '2023-01-01', target + 'T')):
                    add(list(row))
                # Chan uses the last 400 bars, including pre-anchor bars when needed.
                for row in conn.execute(
                    'SELECT substr(trade_date,1,10),open,high,low,close,volume '
                    'FROM stock_daily_prices WHERE symbol=? AND trade_date<? '
                    'ORDER BY trade_date DESC LIMIT 400', (symbol, target + 'T')):
                    add(list(row))
            if 'stock_lifecycle_state' in tables:
                for row in conn.execute('SELECT * FROM stock_lifecycle_state WHERE symbol=? '
                                        'AND trade_date>=? AND trade_date<? ORDER BY trade_date',
                                        (symbol, target, target + 'T')):
                    add(list(row))
        if report.get('source', {}).get('publication_mode') == 'rules':
            from quant_engine.watch.chan_daily_snapshot import FORMULA_VERSION
            add(FORMULA_VERSION)
            for symbol in symbols:
                for table in ('structure_monitor_snapshots', 'structure_monitor_events',
                              'chan_daily_snapshot'):
                    if table in tables:
                        for row in conn.execute(
                            f'SELECT * FROM {table} WHERE trade_date=? AND symbol=? ORDER BY rowid',
                            (target, symbol)):
                            add(list(row))
        if 'data_quality_report' in tables:
            for row in conn.execute('SELECT * FROM data_quality_report WHERE trade_date=? '
                                    'ORDER BY rowid', (target,)):
                add(list(row))
    return digest.hexdigest()


def is_complete(report: dict) -> bool:
    """Trusted deferred or explicitly published rule facts are complete without AI."""
    return report.get('status') == 'READY' and not report.get('missing') and (
        report.get('ai', {}).get('status') in {'READY', 'DEFERRED'} or
        (report.get('source', {}).get('publication_mode') == 'rules'
         and report.get('ai', {}).get('status') in {'UNCONFIGURED', 'FAILED', 'RULES'}))


def _read(db_path: Path | str, target: str, partial: bool, validate: bool) -> dict | None:
    target = _day(target)
    path = directory(db_path) / (target + ('.partial.json' if partial else '.json'))
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        envelope = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(envelope, dict):
            return None
        report = envelope.get('report')
        if (not isinstance(report, dict)
                or not isinstance(report.get('source', {}), dict)
                or not isinstance(report.get('ai', {}), dict)
                or not isinstance(report.get('groups', []), list)
                or any(not isinstance(group, dict) for group in report.get('groups', []))):
            return None
        if (report['date'] != target
                or not re.fullmatch(r'[0-9a-f]{64}', envelope['signature'])
                or not re.fullmatch(re.escape(target) + r'-[0-9a-f]{16}',
                                    envelope.get('archive_id', ''))):
            return None
        if not partial and not is_complete(report):
            return None
        try:
            valid = not validate or _validated_signature(db_path, report, envelope['signature'])
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            return {**envelope, 'valid': False, 'stale_reason': 'source_validation_unavailable'}
        return {**envelope, 'valid': valid,
                'stale_reason': '' if valid else 'source_or_rules_changed'}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return None


def _validated_signature(db_path: Path | str, report: dict, signature: str) -> bool:
    path = Path(db_path).resolve()
    stat = path.stat()
    wal = Path(str(path) + '-wal')
    wal_stat = wal.stat() if wal.exists() else None
    stamps = (stat.st_mtime_ns, stat.st_size,
              wal_stat.st_mtime_ns if wal_stat else None,
              wal_stat.st_size if wal_stat else None)
    versions = (ARCHIVE_VERSION, VERSION, REPORT_VERSION, StructureMonitor(path).config_hash,
                settings.GLM_MODEL, settings.GLM_API_URL, PROMPT_VERSION)
    return _cached_validation(str(path), report['date'], signature,
                              _encode(report.get('groups', [])).decode(), stamps, versions,
                              report.get('source', {}).get('publication_mode', ''))


@lru_cache(maxsize=16)
def _cached_validation(path: str, target: str, signature: str, groups_json: str,
                       stamps: tuple, versions: tuple, publication_mode: str = '') -> bool:
    # Stat/version arguments intentionally invalidate this bounded cache, no connection survives.
    return source_signature(path, {'date': target, 'groups': json.loads(groups_json),
                                  'source': {'publication_mode': publication_mode}}) == signature


def read_archive(db_path: Path | str, target: str, validate: bool = True) -> dict | None:
    return _read(db_path, target, False, validate)


def read_partial(db_path: Path | str, target: str, validate: bool = True) -> dict | None:
    return _read(db_path, target, True, validate)


def _budget(root: Path, destination: Path, incoming: int, retained: int) -> None:
    files = list(root.glob('*.json'))
    total = sum(path.stat().st_size for path in files)
    old_size = destination.stat().st_size if destination.exists() else 0
    needed = total - old_size + incoming + retained
    revisions = sorted((path for path in files
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}-[0-9a-f]{16}\.json', path.name)),
                       key=lambda path: path.stat().st_mtime)
    for path in revisions:
        if needed <= MAX_TOTAL_BYTES:
            break
        needed -= path.stat().st_size
        path.unlink()
    if needed > MAX_TOTAL_BYTES:
        raise ValueError('archive storage budget exceeded')


def write_archive(db_path: Path | str, report: dict, signature: str) -> dict:
    """Atomically save complete or partial reports; failed output cannot replace a complete one."""
    target = _day(report['date'])
    if not re.fullmatch(r'[0-9a-f]{64}', signature):
        raise ValueError('invalid source signature')
    complete = is_complete(report)
    generated_at = datetime.now(timezone.utc).isoformat()
    revision_hash = hashlib.sha256(_encode([report, generated_at])).hexdigest()[:16]
    envelope = dict(report=report, signature=signature,
                    archive_id=target + '-' + revision_hash,
                    generated_at=generated_at,
                    status='READY' if complete else 'PARTIAL')
    data = _encode(envelope)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('archive file exceeds 2 MiB')
    root = directory(db_path)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / (target + ('.json' if complete else '.partial.json'))
    old = read_archive(db_path, target, validate=False) if complete else None
    revision = (root / (old['archive_id'] + '.json')
                if old and old['archive_id'] != envelope['archive_id'] else None)
    retained = destination.stat().st_size if revision and not revision.exists() else 0
    _budget(root, destination, len(data), retained)
    if revision and retained:
        _atomic(revision, destination.read_bytes())
    _atomic(destination, data)
    if complete:
        (root / (target + '.partial.json')).unlink(missing_ok=True)
    return {**envelope, 'valid': True, 'stale_reason': ''}


def _atomic(path: Path, data: bytes) -> None:
    handle, name = tempfile.mkstemp(prefix='.archive-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
