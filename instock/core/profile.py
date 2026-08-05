#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.core.stocklist as stocklist
from instock.core.common import (
    _now,
    _market_phase,
    _previous_trading_day,
    _ensure_cache_tables,
    _PROFILE_CACHE_TABLE,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

_PROFILE_REFRESH_LOCK = threading.Lock()
_PROFILE_REFRESH_RUNNING = False


def _read_profile_cache(db, stock_codes):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT `code`, `market_cap`, `industry_name`, `fetched_at`
        FROM `{_PROFILE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)
    return {row["code"]: row for row in rows}


def _is_profile_cache_stale(cache_row, now):
    if cache_row is None:
        return True
    fetched_at = cache_row.get("fetched_at")
    if not fetched_at:
        return True
    phase = _market_phase(now)
    if phase in ("intraday", "before_open"):
        # 下午3点前使用前一交易日收盘数据
        prev_trading_day = _previous_trading_day(now.date())
        prev_close = datetime.datetime.combine(prev_trading_day, datetime.time(15, 0))
        return fetched_at < prev_close
    if phase == "after_close":
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        return fetched_at < close_time
    return fetched_at.date() < now.date()


def _write_profile_cache(db, code, market_cap, industry_name, now):
    db.execute(f"""
        INSERT INTO `{_PROFILE_CACHE_TABLE}`
            (`code`, `market_cap`, `industry_name`, `fetched_at`)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `market_cap` = VALUES(`market_cap`),
            `industry_name` = VALUES(`industry_name`),
            `fetched_at` = VALUES(`fetched_at`)
    """,
               code,
               market_cap,
               industry_name,
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _refresh_profiles(stock_codes):
    global _PROFILE_REFRESH_RUNNING
    db = None
    try:
        profile_data = stocklist.fetch_profile_data(stock_codes)
        if not profile_data:
            return
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        for code in stock_codes:
            info = profile_data.get(code, {})
            market_cap = info.get("market_cap")
            industry_name = info.get("industry_name", "")
            _write_profile_cache(db, code, market_cap, industry_name, now)
    except Exception as error:
        print(f"profile._refresh_profiles处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _PROFILE_REFRESH_LOCK:
            _PROFILE_REFRESH_RUNNING = False


def _schedule_profile_refresh(stock_codes):
    global _PROFILE_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    with _PROFILE_REFRESH_LOCK:
        if _PROFILE_REFRESH_RUNNING:
            return
        _PROFILE_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_profiles, args=(stock_codes,), daemon=True)
    thread.start()
    return thread


def _get_cached_profile_rows(db, stock_codes):
    """Read profile cache; returns dict keyed by code, missing entries have None value."""
    return _read_profile_cache(db, stock_codes)
