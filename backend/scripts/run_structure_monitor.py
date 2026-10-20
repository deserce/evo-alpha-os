#!/usr/bin/env python3
"""Run only C post-close monitoring; no collection, mail or portfolio writes."""
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import argparse
import json
import logging
import os
import sqlite3

os.environ['EVOALPHA_LOCAL_ONLY'] = '1'
os.environ['EVOALPHA_DISABLE_LLM'] = '1'
os.environ['APP_DEBUG'] = 'false'
for _thread_limit_key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                          'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[_thread_limit_key] = '1'

from quant_engine.watch.structure_monitor import StructureMonitor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--db', type=Path, default=BACKEND_DIR / 'data' / 'local_quant.db')
    parser.add_argument('--workflow-child', action='store_true')
    args = parser.parse_args()
    try:
        result = StructureMonitor(args.db).run(args.trade_date, workflow_child=args.workflow_child)
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        logging.getLogger(__name__).error('C monitor failed: %s', exc)
        return 1
    print(json.dumps({key: result[key] for key in
                      ('trade_date', 'version', 'config_hash', 'scope', 'counts', 'run')},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
