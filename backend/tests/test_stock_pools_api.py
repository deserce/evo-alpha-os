import asyncio
import sqlite3

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine

from app.api.v1 import stock as stock_api


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE stock_info (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            market_cap REAL,
            pe REAL,
            pb REAL
        );
        CREATE TABLE stock_daily_prices (
            symbol TEXT,
            trade_date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            pct_chg REAL,
            turnover_rate REAL
        );
        CREATE TABLE quant_feature_rps (
            symbol TEXT,
            trade_date TEXT,
            rps_5 REAL,
            rps_10 REAL,
            rps_20 REAL,
            rps_50 REAL,
            rps_120 REAL,
            rps_250 REAL
        );
        CREATE TABLE watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT,
            added_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO stock_info VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("688766", "普冉股份", "半导体", 120.0, 30.1, 4.2),
            ("688777", "中控技术", "软件", 200.0, 40.2, 5.3),
        ],
    )
    conn.executemany(
        """
        INSERT INTO stock_daily_prices VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            ("688766", "2026-07-01", 100, 110, 98, 108, 1000, 1200000, 5.2, 2.1),
            ("688777", "2026-07-01", 50, 55, 49, 51, 2000, 1000000, -1.1, 1.8),
        ],
    )
    conn.executemany(
        "INSERT INTO quant_feature_rps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("688766", "2026-07-01 00:00:00.000000", 91, 92, 93, 94, 95, 96),
            ("688777", "2026-07-01 00:00:00.000000", 81, 82, 83, 84, 85, 86),
        ],
    )
    conn.execute(
        "INSERT INTO watchlist (symbol, name, added_at) VALUES ('688766', '普冉股份', '2026-07-02 09:30:00')"
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def stock_db(tmp_path, monkeypatch):
    from data_job import auto_f10

    monkeypatch.setattr(auto_f10, "start_worker", lambda engine: None)
    db_path = tmp_path / "stock_pools.db"
    make_db(db_path)
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(stock_api, "engine", test_engine)
    return db_path


def test_stock_pools_create_default_and_migrate_legacy_watchlist(stock_db):
    data = asyncio.run(stock_api.get_stock_pools()).data

    assert data["default_pool_id"] == "watchlist"
    assert data["pools"][0]["pool_id"] == "watchlist"
    assert data["pools"][0]["monitor_enabled"] == 1
    assert data["pools"][0]["stock_count"] == 1

    stocks = asyncio.run(stock_api.get_stock_pool_stocks("watchlist")).data
    assert stocks["count"] == 1
    assert stocks["stocks"][0]["symbol"] == "688766"
    assert stocks["stocks"][0]["close"] == 108
    assert stocks["stocks"][0]["rps_20"] == 93
    assert stocks["stocks"][0]["rps_250"] == 96


def test_custom_pool_crud_and_default_pool_delete_guard(stock_db):
    created = asyncio.run(
        stock_api.create_stock_pool(stock_api.StockPoolCreate(name="半导体观察"))
    ).data["pool"]

    assert created["name"] == "半导体观察"
    assert created["monitor_enabled"] == 0
    updated = asyncio.run(
        stock_api.update_stock_pool(
            created["pool_id"],
            stock_api.StockPoolUpdate(
                name="重点观察",
                sort_order=3,
                monitor_enabled=True,
            ),
        )
    ).data["pool"]
    assert updated["name"] == "重点观察"
    assert updated["sort_order"] == 3
    assert updated["monitor_enabled"] == 1

    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_api.delete_stock_pool("watchlist"))
    assert exc.value.status_code == 400

    deleted = asyncio.run(stock_api.delete_stock_pool(created["pool_id"])).data
    assert deleted["deleted"] is True


def test_add_duplicate_remove_and_multi_pool_membership(stock_db):
    pool_a = asyncio.run(
        stock_api.create_stock_pool(stock_api.StockPoolCreate(name="AI芯片"))
    ).data["pool"]
    pool_b = asyncio.run(
        stock_api.create_stock_pool(stock_api.StockPoolCreate(name="等回调"))
    ).data["pool"]

    asyncio.run(
        stock_api.add_stock_pool_member(
            pool_a["pool_id"],
            stock_api.StockPoolMemberCreate(symbol="688766", note="第一次"),
        )
    )
    repeated = asyncio.run(
        stock_api.add_stock_pool_member(
            pool_a["pool_id"],
            stock_api.StockPoolMemberCreate(symbol="688766", note="更新备注"),
        )
    ).data["stock"]
    asyncio.run(
        stock_api.add_stock_pool_member(
            pool_b["pool_id"],
            stock_api.StockPoolMemberCreate(symbol="688766"),
        )
    )

    assert repeated["note"] == "更新备注"
    assert asyncio.run(stock_api.get_stock_pool_stocks(pool_a["pool_id"])).data["count"] == 1
    assert asyncio.run(stock_api.get_stock_pool_stocks(pool_b["pool_id"])).data["count"] == 1

    removed = asyncio.run(
        stock_api.delete_stock_pool_member(pool_a["pool_id"], "688766")
    ).data
    assert removed["deleted"] is True
    assert asyncio.run(stock_api.get_stock_pool_stocks(pool_a["pool_id"])).data["count"] == 0
    assert asyncio.run(stock_api.get_stock_pool_stocks(pool_b["pool_id"])).data["count"] == 1


def test_add_pool_member_rejects_unknown_symbol(stock_db):
    pool = asyncio.run(
        stock_api.create_stock_pool(stock_api.StockPoolCreate(name="测试池"))
    ).data["pool"]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            stock_api.add_stock_pool_member(
                pool["pool_id"],
                stock_api.StockPoolMemberCreate(symbol="999999"),
            )
        )

    assert exc.value.status_code == 404


def test_new_member_in_monitored_pool_queues_targeted_watch_refresh(
    stock_db, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        stock_api,
        "_run_member_refresh_background",
        lambda symbols: calls.append(symbols),
    )
    tasks = BackgroundTasks()

    response = asyncio.run(
        stock_api.add_stock_pool_member(
            "watchlist",
            stock_api.StockPoolMemberCreate(symbol="688777"),
            tasks,
        )
    ).data
    asyncio.run(tasks())

    assert response["refresh_queued"] is True
    assert calls == [["688777"]]


def test_duplicate_or_unmonitored_member_does_not_queue_refresh(stock_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        stock_api,
        "_run_member_refresh_background",
        lambda symbols: calls.append(symbols),
    )
    duplicate_tasks = BackgroundTasks()
    duplicate = asyncio.run(
        stock_api.add_stock_pool_member(
            "watchlist",
            stock_api.StockPoolMemberCreate(symbol="688766", note="只改备注"),
            duplicate_tasks,
        )
    ).data
    asyncio.run(duplicate_tasks())

    pool = asyncio.run(
        stock_api.create_stock_pool(stock_api.StockPoolCreate(name="暂不监控"))
    ).data["pool"]
    disabled_tasks = BackgroundTasks()
    disabled = asyncio.run(
        stock_api.add_stock_pool_member(
            pool["pool_id"],
            stock_api.StockPoolMemberCreate(symbol="688777"),
            disabled_tasks,
        )
    ).data
    asyncio.run(disabled_tasks())

    assert duplicate["refresh_queued"] is False
    assert disabled["refresh_queued"] is False
    assert calls == []


def test_enabling_pool_monitoring_queues_existing_members(stock_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        stock_api,
        "_run_member_refresh_background",
        lambda symbols: calls.append(symbols),
    )
    pool = asyncio.run(
        stock_api.create_stock_pool(stock_api.StockPoolCreate(name="稍后开启"))
    ).data["pool"]
    asyncio.run(
        stock_api.add_stock_pool_member(
            pool["pool_id"],
            stock_api.StockPoolMemberCreate(symbol="688777"),
        )
    )
    tasks = BackgroundTasks()

    updated = asyncio.run(
        stock_api.update_stock_pool(
            pool["pool_id"],
            stock_api.StockPoolUpdate(monitor_enabled=True),
            tasks,
        )
    ).data
    asyncio.run(tasks())

    assert updated["refresh_queued"] is True
    assert calls == [["688777"]]


def test_existing_stock_pool_schema_is_safely_migrated(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy_stock_pools.db"
    make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stock_pools (
            pool_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            sort_order INTEGER DEFAULT 0,
            is_default INTEGER DEFAULT 0,
            legacy_migrated INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE stock_pool_members (
            pool_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            note TEXT,
            sort_order INTEGER DEFAULT 0,
            added_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (pool_id, symbol)
        );
        INSERT INTO stock_pools VALUES
            ('watchlist', '自选股', NULL, 0, 1, 1, NULL, NULL),
            ('pool_old', '旧分类', NULL, 10, 0, 1, NULL, NULL);
        """
    )
    conn.commit()
    conn.close()

    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(stock_api, "engine", test_engine)

    pools = {
        row["pool_id"]: row
        for row in asyncio.run(stock_api.get_stock_pools()).data["pools"]
    }
    assert pools["watchlist"]["monitor_enabled"] == 1
    assert pools["pool_old"]["monitor_enabled"] == 0


def test_unmonitored_new_member_queues_materials_once(stock_db, monkeypatch):
    from data_job import auto_f10
    calls = []
    monkeypatch.setattr(auto_f10, 'enqueue_symbols',
                        lambda symbols, **kwargs: calls.append(symbols))
    pool = asyncio.run(stock_api.create_stock_pool(
        stock_api.StockPoolCreate(name='资料自动下载测试'))).data['pool']
    for _ in range(2):
        tasks = BackgroundTasks()
        asyncio.run(stock_api.add_stock_pool_member(
            pool['pool_id'], stock_api.StockPoolMemberCreate(symbol='688777'), tasks))
        asyncio.run(tasks())
    assert calls == [['688777']]


@pytest.mark.parametrize('operation', ['rename', 'delete', 'add', 'remove', 'note'])
def test_smart_pool_rejects_manual_mutations(stock_db, operation):
    asyncio.run(stock_api.get_stock_pools())
    with sqlite3.connect(stock_db) as conn:
        conn.execute("INSERT INTO stock_pools(pool_id,name) VALUES('smart_mrgc_20d','MRGC · 近20日')")
    actions = {
        'rename': lambda: stock_api.update_stock_pool('smart_mrgc_20d', stock_api.StockPoolUpdate(name='改名')),
        'delete': lambda: stock_api.delete_stock_pool('smart_mrgc_20d'),
        'add': lambda: stock_api.add_stock_pool_member('smart_mrgc_20d', stock_api.StockPoolMemberCreate(symbol='688766')),
        'remove': lambda: stock_api.delete_stock_pool_member('smart_mrgc_20d', '688766'),
        'note': lambda: stock_api.update_stock_pool_member('smart_mrgc_20d', '688766', stock_api.StockPoolMemberUpdate(note='x')),
    }
    with pytest.raises(HTTPException) as exc:
        asyncio.run(actions[operation]())
    assert exc.value.status_code == 409


@pytest.fixture()
def historical_smart_db(stock_db):
    asyncio.run(stock_api.get_stock_pools())
    from datetime import date, timedelta
    days = [(date(2026, 6, 12) + timedelta(days=i)).isoformat() for i in range(21)]
    with sqlite3.connect(stock_db) as conn:
        conn.executescript("""
            CREATE TABLE strategy_run_log(trade_date TEXT,strategy_name TEXT,status TEXT);
            CREATE TABLE quant_preselect_results(trade_date TEXT,strategy_name TEXT,symbol TEXT);
            CREATE TABLE trading_calendar(trade_date TEXT,is_open INTEGER);
            INSERT INTO stock_pools(pool_id,name) VALUES('smart_mrgc_20d','MRGC · 近20日');
            INSERT INTO stock_pool_members(pool_id,symbol) VALUES('smart_mrgc_20d','688777');
        """)
        for day in days:
            conn.execute('INSERT INTO trading_calendar VALUES(?,1)', (day,))
            conn.execute("INSERT INTO strategy_run_log VALUES(?,'mrgc_v1','success')", (day,))
            if day != "2026-07-01":
                conn.execute("INSERT INTO stock_daily_prices(symbol,trade_date,close) VALUES('688766',?,108)", (day,))
        conn.executemany("INSERT INTO quant_preselect_results VALUES(?,'mrgc_v1',?)",
                         [(days[0], '688766'), (days[-1], '688777')])
    return stock_db, days


def test_smart_pool_historical_members_exclude_future_and_change_by_date(historical_smart_db):
    path, days = historical_smart_db
    earlier = asyncio.run(stock_api.get_stock_pool_stocks('smart_mrgc_20d', days[-2])).data
    later = asyncio.run(stock_api.get_stock_pool_stocks('smart_mrgc_20d', days[-1])).data
    assert [row['symbol'] for row in earlier['stocks']] == ['688766']
    assert [row['symbol'] for row in later['stocks']] == ['688777']
    assert earlier['pool']['stock_count'] == 1
    assert earlier['pool']['smart_state']['target_trade_date'] == days[-2]
    pools = asyncio.run(stock_api.get_stock_pools(days[-2])).data['pools']
    smart = next(row for row in pools if row['pool_id'] == 'smart_mrgc_20d')
    assert smart['smart_state']['window_end'] == days[-2]
    assert smart['stock_count'] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT symbol FROM stock_pool_members WHERE pool_id='smart_mrgc_20d'").fetchall() == [('688777',)]
    manual = asyncio.run(stock_api.get_stock_pool_stocks('watchlist', days[-2])).data
    assert [row['symbol'] for row in manual['stocks']] == ['688766']


def test_smart_pool_missing_screening_is_explicit(historical_smart_db):
    path, days = historical_smart_db
    with sqlite3.connect(path) as conn:
        conn.execute('DELETE FROM strategy_run_log WHERE trade_date=?', (days[-2],))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_api.get_stock_pool_stocks('smart_mrgc_20d', days[-2]))
    assert exc.value.status_code == 409
    assert exc.value.detail['reason'] == 'mrgc_dates_missing'
    pools = asyncio.run(stock_api.get_stock_pools(days[-2])).data['pools']
    smart = next(row for row in pools if row['pool_id'] == 'smart_mrgc_20d')
    assert smart['stock_count'] == 0
    assert smart['smart_state']['status'] == 'BLOCKED'
