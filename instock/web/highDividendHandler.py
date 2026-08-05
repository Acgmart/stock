#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import datetime
import hashlib
import json
import threading
import time
from decimal import Decimal
from zoneinfo import ZoneInfo

import instock.lib.database as mdb
import instock.lib.mysql as mysql
from instock.core.eastmoney_fetcher import eastmoney_fetcher
import instock.core.stocklist as stocklist
import instock.core.followlist as followlist
import instock.core.blocklist as blocklist
import instock.web.base as webBase

__author__ = 'myh '
__date__ = '2026/5/12 '

_DIVIDEND_FETCHER = eastmoney_fetcher()
_CACHE_TABLE_READY = False
_CACHE_TABLE_LOCK = threading.Lock()
_EXTERNAL_REQUEST_LOCK = threading.Lock()
_DIVIDEND_REFRESH_LOCK = threading.Lock()
_MA120_REFRESH_LOCK = threading.Lock()
_LOW20_REFRESH_LOCK = threading.Lock()
_DIVIDEND_REFRESH_RUNNING = False
_MA120_REFRESH_RUNNING = False
_MA120_REFRESH_ATTEMPTS = {}
_LOW20_REFRESH_RUNNING = False
_LOW20_REFRESH_ATTEMPTS = {}
_HIGH20_REFRESH_LOCK = threading.Lock()
_HIGH20_REFRESH_RUNNING = False
_HIGH20_REFRESH_ATTEMPTS = {}
_LAST_EXTERNAL_REQUEST_AT = 0.0
_EXTERNAL_REQUEST_INTERVAL_SECONDS = 2
_PRICE_CACHE_TABLE = "cn_high_dividend_price_cache"
_DIVIDEND_HISTORY_CACHE_TABLE = "cn_high_dividend_dividend_history_cache"
_FINANCE_REPORT_CACHE_TABLE = "cn_high_dividend_finance_report_cache"
_CASHFLOW_CACHE_TABLE = "cn_high_dividend_cashflow_cache"
_MA120_CACHE_TABLE = "cn_high_dividend_ma120_cache"
_LOW20_CACHE_TABLE = "cn_high_dividend_low20_cache"
_HIGH20_CACHE_TABLE = "cn_high_dividend_high20_cache"
_PROFILE_CACHE_TABLE = "cn_high_dividend_profile_cache"
_FINANCE_REPORT_REFRESH_LOCK = threading.Lock()
_CASHFLOW_REFRESH_LOCK = threading.Lock()
_PROFILE_REFRESH_LOCK = threading.Lock()
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_PRICE_REFRESH_MINUTES = 30
_DIVIDEND_REFRESH_HOUR = 8
_DIVIDEND_AFTER_CLOSE_REFRESH_START_HOUR = 16
_DIVIDEND_AFTER_CLOSE_REFRESH_END_HOUR = 23
_REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS = 4
_CASHFLOW_OFFSEASON_REFRESH_DAYS = 7
_FINANCE_REPORT_REFRESH_RUNNING = False
_CASHFLOW_REFRESH_RUNNING = False
_PROFILE_REFRESH_RUNNING = False


def _to_float(value):
    try:
        if value in ("", None, "--", "-"):
            return None
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    except Exception:
        return None


def _now():
    return datetime.datetime.now(_SHANGHAI_TZ).replace(tzinfo=None)


def _date_text(value):
    if not value:
        return ""
    return str(value)[:10]


def _json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _throttle_external_request():
    global _LAST_EXTERNAL_REQUEST_AT
    with _EXTERNAL_REQUEST_LOCK:
        elapsed = time.time() - _LAST_EXTERNAL_REQUEST_AT
        if elapsed < _EXTERNAL_REQUEST_INTERVAL_SECONDS:
            time.sleep(_EXTERNAL_REQUEST_INTERVAL_SECONDS - elapsed)
        _LAST_EXTERNAL_REQUEST_AT = time.time()


def _ensure_cache_tables(db):
    global _CACHE_TABLE_READY
    if _CACHE_TABLE_READY:
        return

    with _CACHE_TABLE_LOCK:
        if _CACHE_TABLE_READY:
            return

        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_PRICE_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `price_date` date DEFAULT NULL,
                `current_price` decimal(12,4) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                `market_phase` varchar(20) DEFAULT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_PRICE_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `pre_close_price` decimal(12,4) DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_PROFILE_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `market_cap` decimal(16,4) DEFAULT NULL,
                `industry_name` varchar(50) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_DIVIDEND_HISTORY_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `history_json` longtext NOT NULL,
                `history_hash` char(64) DEFAULT NULL,
                `checked_on` date NOT NULL,
                `checked_at` datetime NOT NULL,
                `changed_at` datetime DEFAULT NULL,
                `changed_report_date` date DEFAULT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_checked_on` (`checked_on`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_DIVIDEND_HISTORY_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `history_hash` char(64) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_DIVIDEND_HISTORY_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_at` datetime DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_DIVIDEND_HISTORY_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_report_date` date DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_FINANCE_REPORT_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `finance_json` longtext NOT NULL,
                `finance_hash` char(64) DEFAULT NULL,
                `checked_on` date NOT NULL,
                `checked_at` datetime NOT NULL,
                `changed_at` datetime DEFAULT NULL,
                `changed_report_date` date DEFAULT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_checked_on` (`checked_on`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_FINANCE_REPORT_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `finance_hash` char(64) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_FINANCE_REPORT_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_at` datetime DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_FINANCE_REPORT_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `changed_report_date` date DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_CASHFLOW_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `name` varchar(20) DEFAULT NULL,
                `cashflow_json` longtext NOT NULL,
                `checked_on` date NOT NULL,
                `checked_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_checked_on` (`checked_on`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_MA120_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date DEFAULT NULL,
                `close_price` decimal(12,4) DEFAULT NULL,
                `ma120` decimal(12,4) DEFAULT NULL,
                `ma120_position` decimal(12,4) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_LOW20_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date DEFAULT NULL,
                `close_price` decimal(12,4) DEFAULT NULL,
                `lowest_date` date DEFAULT NULL,
                `lowest_low` decimal(12,4) DEFAULT NULL,
                `bounce_position` decimal(12,4) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_LOW20_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `lowest_date` date DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_LOW20_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `lowest_low` decimal(12,4) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_LOW20_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `bounce_position` decimal(12,4) DEFAULT NULL")
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_HIGH20_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `trade_date` date DEFAULT NULL,
                `close_price` decimal(12,4) DEFAULT NULL,
                `highest_date` date DEFAULT NULL,
                `highest_high` decimal(12,4) DEFAULT NULL,
                `decline_position` decimal(12,4) DEFAULT NULL,
                `fetched_at` datetime NOT NULL,
                PRIMARY KEY (`code`),
                INDEX `idx_fetched_at` (`fetched_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_HIGH20_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `highest_date` date DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_HIGH20_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `highest_high` decimal(12,4) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_HIGH20_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `decline_position` decimal(12,4) DEFAULT NULL")
        _CACHE_TABLE_READY = True


def _market_phase(now):
    if now.weekday() >= 5:
        return "closed"
    current = now.time()
    if datetime.time(9, 30) <= current < datetime.time(15, 0):
        return "intraday"
    if current >= datetime.time(15, 0):
        return "after_close"
    return "before_open"


def _is_price_cache_stale(cached_rows, stock_codes, now):
    cached_codes = {row["code"] for row in cached_rows}
    if any(code not in cached_codes for code in stock_codes):
        return True
    if not cached_rows:
        return True
    if any((_to_float(row.get("current_price")) or 0) <= 0 for row in cached_rows):
        return True

    latest_fetched_at = max(row["fetched_at"] for row in cached_rows if row.get("fetched_at"))
    phase = _market_phase(now)
    if phase == "intraday":
        return latest_fetched_at <= now - datetime.timedelta(minutes=_PRICE_REFRESH_MINUTES)
    if phase == "after_close":
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        return latest_fetched_at < close_time or latest_fetched_at.date() != now.date()
    return False


def _read_price_cache(db, stock_codes):
    if not stock_codes:
        return []
    placeholders = ",".join(["%s"] * len(stock_codes))
    return db.query(f"""
        SELECT `code`, `name`, `price_date`, `current_price`, `pre_close_price`, `fetched_at`, `market_phase`
        FROM `{_PRICE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)


def _write_price_cache(db, price_data, now):
    phase = _market_phase(now)
    for row in price_data:
        current_price = _to_float(row.get("new_price"))
        pre_close_price = _to_float(row.get("pre_close_price"))
        if current_price is None or current_price <= 0:
            current_price = pre_close_price
        db.execute(f"""
            INSERT INTO `{_PRICE_CACHE_TABLE}`
                (`code`, `name`, `price_date`, `current_price`, `pre_close_price`, `fetched_at`, `market_phase`)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `name` = VALUES(`name`),
                `price_date` = VALUES(`price_date`),
                `current_price` = VALUES(`current_price`),
                `pre_close_price` = VALUES(`pre_close_price`),
                `fetched_at` = VALUES(`fetched_at`),
                `market_phase` = VALUES(`market_phase`)
        """,
                   row.get("code"),
                   row.get("name"),
                   row.get("date"),
                   current_price,
                   pre_close_price,
                   now.strftime("%Y-%m-%d %H:%M:%S"),
                   phase)


def _sync_indicator_cache_for_prices(db, stock_codes, now):
    """用最新股价同步更新 MA120/反弹/回落缓存中的 close_price 和百分比值。"""
    price_rows = _read_price_cache(db, stock_codes)
    price_by_code = {row["code"]: _to_float(row.get("current_price")) for row in price_rows}

    # MA120
    ma120_rows = _read_ma120_cache(db, stock_codes)
    for code, row in ma120_rows.items():
        current_price = price_by_code.get(code)
        if current_price is None or current_price <= 0:
            continue
        ma120 = _to_float(row.get("ma120"))
        if ma120 is None or ma120 <= 0:
            continue
        new_ma120_pos = (current_price / ma120 - 1) * 100
        db.execute(f"""
            UPDATE `{_MA120_CACHE_TABLE}`
            SET `close_price` = %s, `ma120_position` = %s, `fetched_at` = %s
            WHERE `code` = %s
        """, current_price, new_ma120_pos, now.strftime("%Y-%m-%d %H:%M:%S"), code)

    # 反弹
    low20_rows = _read_low20_cache(db, stock_codes)
    for code, row in low20_rows.items():
        current_price = price_by_code.get(code)
        if current_price is None or current_price <= 0:
            continue
        lowest_low = _to_float(row.get("lowest_low"))
        if lowest_low is None or lowest_low <= 0:
            continue
        new_bounce = (current_price / lowest_low - 1) * 100
        db.execute(f"""
            UPDATE `{_LOW20_CACHE_TABLE}`
            SET `close_price` = %s, `bounce_position` = %s, `fetched_at` = %s
            WHERE `code` = %s
        """, current_price, new_bounce, now.strftime("%Y-%m-%d %H:%M:%S"), code)

    # 回落
    high20_rows = _read_high20_cache(db, stock_codes)
    for code, row in high20_rows.items():
        current_price = price_by_code.get(code)
        if current_price is None or current_price <= 0:
            continue
        highest_high = _to_float(row.get("highest_high"))
        if highest_high is None or highest_high <= 0:
            continue
        new_decline = (current_price / highest_high - 1) * 100
        db.execute(f"""
            UPDATE `{_HIGH20_CACHE_TABLE}`
            SET `close_price` = %s, `decline_position` = %s, `fetched_at` = %s
            WHERE `code` = %s
        """, current_price, new_decline, now.strftime("%Y-%m-%d %H:%M:%S"), code)


def _get_cached_price_rows(db, stock_codes, errors):
    now = _now()
    cached_rows = _read_price_cache(db, stock_codes)
    if _is_price_cache_stale(cached_rows, stock_codes, now):
        try:
            price_data = stocklist.make_selected_stock_rows(now.date())
            if price_data is not None:
                _write_price_cache(db, price_data, now)
                cached_rows = _read_price_cache(db, stock_codes)
                # 同步更新 MA120/反弹/回落缓存
                try:
                    _sync_indicator_cache_for_prices(db, stock_codes, now)
                except Exception:
                    pass
        except Exception as error:
            errors.append(f"行情缓存刷新失败，已使用旧缓存：{error}")
    return {row["code"]: row for row in cached_rows}


def _read_ma120_cache(db, stock_codes):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT `code`, `trade_date`, `close_price`, `ma120`, `ma120_position`, `fetched_at`
        FROM `{_MA120_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)
    return {row["code"]: row for row in rows}


def _previous_trading_day(date):
    """返回 date 之前的最近一个交易日（周一至周五），跨周末时回到周五。"""
    prev = date - datetime.timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= datetime.timedelta(days=1)
    return prev


_MA120_STAGE_PERCENT = 10


def _ma120_stage(position):
    """MA120 相对位置所处阶段序号，每 10% 为一个阶段（向下取整）。"""
    return int(position // _MA120_STAGE_PERCENT)


def _ma120_trade_signal(current_price, pre_close_price, ma120_position, ma120):
    """判断 MA120 位置的买卖点信号。

    以 MA120 相对位置每 10% 为一个阶段，股价跨越阶段边界时触发提示：
    买点：股价相对昨日收盘价下跌、最新位置位于 0% 以下，且从更高阶段跨入更低阶段。
    卖点：股价相对昨日收盘价上涨、最新位置位于 0% 以上，且从更低阶段跨入更高阶段。
    返回 "buy"、"sell" 或空字符串。
    """
    if current_price is None or pre_close_price is None or pre_close_price <= 0:
        return ""
    if ma120_position is None or ma120 is None or ma120 <= 0:
        return ""
    prev_position = (pre_close_price / ma120 - 1) * 100
    stage_diff = _ma120_stage(prev_position) - _ma120_stage(ma120_position)
    if current_price < pre_close_price and ma120_position < 0 and stage_diff > 0:
        return "buy"
    if current_price > pre_close_price and ma120_position > 0 and stage_diff < 0:
        return "sell"
    return ""


def _is_ma120_cache_stale(cache_row, now):
    phase = _market_phase(now)
    if cache_row is None:
        return True
    fetched_at = cache_row.get("fetched_at")
    if not fetched_at:
        return True
    if phase in ("intraday", "before_open"):
        # 下午3点前使用前一交易日收盘数据
        prev_trading_day = _previous_trading_day(now.date())
        prev_close = datetime.datetime.combine(prev_trading_day, datetime.time(15, 0))
        return fetched_at < prev_close
    if phase == "after_close":
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        return fetched_at < close_time
    return fetched_at.date() < now.date()


def _ma120_refresh_window(now):
    phase = _market_phase(now)
    if phase == "before_open":
        phase = "intraday"
    return f"{now.date()}:{phase}"


def _write_ma120_cache(db, code, ma120_row, now):
    db.execute(f"""
        INSERT INTO `{_MA120_CACHE_TABLE}`
            (`code`, `trade_date`, `close_price`, `ma120`, `ma120_position`, `fetched_at`)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `trade_date` = VALUES(`trade_date`),
            `close_price` = VALUES(`close_price`),
            `ma120` = VALUES(`ma120`),
            `ma120_position` = VALUES(`ma120_position`),
            `fetched_at` = VALUES(`fetched_at`)
    """,
               code,
               ma120_row.get("trade_date"),
               ma120_row.get("close_price"),
               ma120_row.get("ma120"),
               ma120_row.get("ma120_position"),
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _refresh_ma120_positions(stock_codes):
    global _MA120_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        phase = _market_phase(now)
        # 下午3点前：日期朝前一交易日挪，排除当日未完成K线
        effective_today = now.date() if phase in ("intraday", "before_open") else None
        # 读取实时价格，优先用于 MA120 位置计算
        price_rows = _read_price_cache(db, stock_codes)
        price_by_code = {row["code"]: row for row in price_rows}
        for code in stock_codes:
            cache_row = _read_ma120_cache(db, [code]).get(code)
            if not _is_ma120_cache_stale(cache_row, now):
                continue

            price_row = price_by_code.get(code)
            current_price = _to_float(price_row.get("current_price")) if price_row else None
            ma120_row = stocklist.fetch_daily_ma120_position(code, today=effective_today, current_price=current_price)
            if ma120_row is not None:
                _write_ma120_cache(db, code, ma120_row, now)
    except Exception as error:
        print(f"highDividendHandler._refresh_ma120_positions处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _MA120_REFRESH_LOCK:
            _MA120_REFRESH_RUNNING = False


def _schedule_ma120_refresh(stock_codes):
    global _MA120_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    window = _ma120_refresh_window(_now())
    if window is None:
        return
    with _MA120_REFRESH_LOCK:
        if _MA120_REFRESH_RUNNING:
            return
        stock_codes = tuple(
            code for code in stock_codes
            if _MA120_REFRESH_ATTEMPTS.get(code) != window
        )
        if not stock_codes:
            return
        for code in stock_codes:
            _MA120_REFRESH_ATTEMPTS[code] = window
        _MA120_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_ma120_positions, args=(stock_codes,), daemon=True)
    thread.start()


def _read_low20_cache(db, stock_codes):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT `code`, `trade_date`, `close_price`, `lowest_date`, `lowest_low`,
               `bounce_position`, `fetched_at`
        FROM `{_LOW20_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)
    return {row["code"]: row for row in rows}


def _is_low20_cache_stale(cache_row, now):
    phase = _market_phase(now)
    if cache_row is None:
        return True
    fetched_at = cache_row.get("fetched_at")
    if not fetched_at:
        return True
    if phase in ("intraday", "before_open"):
        # 下午3点前使用前一交易日收盘数据
        prev_trading_day = _previous_trading_day(now.date())
        prev_close = datetime.datetime.combine(prev_trading_day, datetime.time(15, 0))
        return fetched_at < prev_close
    if phase == "after_close":
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        return fetched_at < close_time
    return fetched_at.date() < now.date()


def _low20_refresh_window(now):
    phase = _market_phase(now)
    if phase == "before_open":
        phase = "intraday"
    return f"{now.date()}:{phase}"


def _write_low20_cache(db, code, low20_row, now):
    db.execute(f"""
        INSERT INTO `{_LOW20_CACHE_TABLE}`
            (`code`, `trade_date`, `close_price`, `lowest_date`, `lowest_low`,
             `bounce_position`, `fetched_at`)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `trade_date` = VALUES(`trade_date`),
            `close_price` = VALUES(`close_price`),
            `lowest_date` = VALUES(`lowest_date`),
            `lowest_low` = VALUES(`lowest_low`),
            `bounce_position` = VALUES(`bounce_position`),
            `fetched_at` = VALUES(`fetched_at`)
    """,
               code,
               low20_row.get("trade_date"),
               low20_row.get("close_price"),
               low20_row.get("lowest_date"),
               low20_row.get("lowest_low"),
               low20_row.get("bounce_position"),
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _refresh_low20_positions(stock_codes):
    global _LOW20_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        phase = _market_phase(now)
        # 下午3点前：日期朝前一交易日挪，排除当日未完成K线
        effective_today = now.date() if phase in ("intraday", "before_open") else None
        # 读取实时价格，优先用于反弹幅度计算
        price_rows = _read_price_cache(db, stock_codes)
        price_by_code = {row["code"]: row for row in price_rows}
        for code in stock_codes:
            cache_row = _read_low20_cache(db, [code]).get(code)
            if not _is_low20_cache_stale(cache_row, now):
                continue

            price_row = price_by_code.get(code)
            current_price = _to_float(price_row.get("current_price")) if price_row else None
            low20_row = stocklist.fetch_20day_low_bounce(code, today=effective_today, current_price=current_price)
            if low20_row is not None:
                _write_low20_cache(db, code, low20_row, now)
    except Exception as error:
        print(f"highDividendHandler._refresh_low20_positions处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _LOW20_REFRESH_LOCK:
            _LOW20_REFRESH_RUNNING = False


def _schedule_low20_refresh(stock_codes):
    global _LOW20_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    window = _low20_refresh_window(_now())
    if window is None:
        return
    with _LOW20_REFRESH_LOCK:
        if _LOW20_REFRESH_RUNNING:
            return
        stock_codes = tuple(
            code for code in stock_codes
            if _LOW20_REFRESH_ATTEMPTS.get(code) != window
        )
        if not stock_codes:
            return
        for code in stock_codes:
            _LOW20_REFRESH_ATTEMPTS[code] = window
        _LOW20_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_low20_positions, args=(stock_codes,), daemon=True)
    thread.start()


def _read_high20_cache(db, stock_codes):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT `code`, `trade_date`, `close_price`, `highest_date`, `highest_high`,
               `decline_position`, `fetched_at`
        FROM `{_HIGH20_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)
    return {row["code"]: row for row in rows}


def _is_high20_cache_stale(cache_row, now):
    phase = _market_phase(now)
    if cache_row is None:
        return True
    fetched_at = cache_row.get("fetched_at")
    if not fetched_at:
        return True
    if phase in ("intraday", "before_open"):
        # 下午3点前使用前一交易日收盘数据
        prev_trading_day = _previous_trading_day(now.date())
        prev_close = datetime.datetime.combine(prev_trading_day, datetime.time(15, 0))
        return fetched_at < prev_close
    if phase == "after_close":
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        return fetched_at < close_time
    return fetched_at.date() < now.date()


def _high20_refresh_window(now):
    phase = _market_phase(now)
    if phase == "before_open":
        phase = "intraday"
    return f"{now.date()}:{phase}"


def _write_high20_cache(db, code, high20_row, now):
    db.execute(f"""
        INSERT INTO `{_HIGH20_CACHE_TABLE}`
            (`code`, `trade_date`, `close_price`, `highest_date`, `highest_high`,
             `decline_position`, `fetched_at`)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `trade_date` = VALUES(`trade_date`),
            `close_price` = VALUES(`close_price`),
            `highest_date` = VALUES(`highest_date`),
            `highest_high` = VALUES(`highest_high`),
            `decline_position` = VALUES(`decline_position`),
            `fetched_at` = VALUES(`fetched_at`)
    """,
               code,
               high20_row.get("trade_date"),
               high20_row.get("close_price"),
               high20_row.get("highest_date"),
               high20_row.get("highest_high"),
               high20_row.get("decline_position"),
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _refresh_high20_positions(stock_codes):
    global _HIGH20_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        phase = _market_phase(now)
        # 下午3点前：日期朝前一交易日挪，排除当日未完成K线
        effective_today = now.date() if phase in ("intraday", "before_open") else None
        # 读取实时价格，优先用于回落幅度计算
        price_rows = _read_price_cache(db, stock_codes)
        price_by_code = {row["code"]: row for row in price_rows}
        for code in stock_codes:
            cache_row = _read_high20_cache(db, [code]).get(code)
            if not _is_high20_cache_stale(cache_row, now):
                continue

            price_row = price_by_code.get(code)
            current_price = _to_float(price_row.get("current_price")) if price_row else None
            high20_row = stocklist.fetch_20day_high_decline(code, today=effective_today, current_price=current_price)
            if high20_row is not None:
                _write_high20_cache(db, code, high20_row, now)
    except Exception as error:
        print(f"highDividendHandler._refresh_high20_positions处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _HIGH20_REFRESH_LOCK:
            _HIGH20_REFRESH_RUNNING = False


def _schedule_high20_refresh(stock_codes):
    global _HIGH20_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    window = _high20_refresh_window(_now())
    if window is None:
        return
    with _HIGH20_REFRESH_LOCK:
        if _HIGH20_REFRESH_RUNNING:
            return
        stock_codes = tuple(
            code for code in stock_codes
            if _HIGH20_REFRESH_ATTEMPTS.get(code) != window
        )
        if not stock_codes:
            return
        for code in stock_codes:
            _HIGH20_REFRESH_ATTEMPTS[code] = window
        _HIGH20_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_high20_positions, args=(stock_codes,), daemon=True)
    thread.start()


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
        print(f"highDividendHandler._refresh_profiles处理异常：{error}")
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


def _get_cached_profile_rows(db, stock_codes):
    """Read profile cache; returns dict keyed by code, missing entries have None value."""
    return _read_profile_cache(db, stock_codes)


def _fetch_dividend_history(code):
    _throttle_external_request()
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "pageSize": "100",
        "pageNumber": "1",
        "reportName": "RPT_SHAREBONUS_DET",
        "columns": "ALL",
        "quoteColumns": "",
        "source": "WEB",
        "client": "WEB",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    response = _DIVIDEND_FETCHER.make_request(url, params=params, timeout=15)
    payload = response.json()
    if not payload.get("success") or not payload.get("result"):
        return []
    return payload["result"].get("data") or []


def _history_name(history):
    for item in history:
        name = item.get("SECURITY_NAME_ABBR")
        if name:
            return name
    return ""


def _history_hash(history):
    history_text = json.dumps(history, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha256(history_text.encode("utf-8")).hexdigest()


def _read_dividend_history_cache(db, code):
    row = db.get(f"""
        SELECT `code`, `name`, `history_json`, `history_hash`, `checked_on`, `checked_at`,
               `changed_at`, `changed_report_date`
        FROM `{_DIVIDEND_HISTORY_CACHE_TABLE}`
        WHERE `code` = %s
    """, code)
    if row is None:
        return None, []
    try:
        return row, json.loads(row["history_json"])
    except Exception:
        return row, []


def _is_dividend_history_cache_stale(cache_row, history, now):
    return _is_daily_report_cache_stale(
        cache_row,
        now,
        after_close_interval_hours=_REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS
    )


def _changed_report_date(now):
    if now.hour >= _DIVIDEND_AFTER_CLOSE_REFRESH_START_HOUR:
        return now.date() + datetime.timedelta(days=1)
    return now.date()


def _is_daily_report_cache_stale(cache_row, now, after_close_interval_hours=None, after_close_once=False):
    if cache_row is None:
        return True

    checked_on = cache_row.get("checked_on")
    checked_at = cache_row.get("checked_at")
    if not checked_on or not checked_at:
        return True
    if isinstance(checked_on, datetime.datetime):
        checked_on = checked_on.date()

    if now.weekday() >= 5:
        return False

    if _DIVIDEND_AFTER_CLOSE_REFRESH_START_HOUR <= now.hour <= _DIVIDEND_AFTER_CLOSE_REFRESH_END_HOUR:
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        if after_close_once:
            return checked_at < close_time
        interval_hours = after_close_interval_hours or _REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS
        return checked_at <= now - datetime.timedelta(hours=interval_hours)
    if now.time() >= datetime.time(_DIVIDEND_REFRESH_HOUR, 0):
        return checked_on < now.date()
    return False


def _write_dividend_history_cache(db, code, history, now, changed):
    changed_at = now.strftime("%Y-%m-%d %H:%M:%S") if changed else None
    changed_report_date = _changed_report_date(now).strftime("%Y-%m-%d") if changed else None
    db.execute(f"""
        INSERT INTO `{_DIVIDEND_HISTORY_CACHE_TABLE}`
            (`code`, `name`, `history_json`, `history_hash`, `checked_on`, `checked_at`,
             `changed_at`, `changed_report_date`)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `name` = VALUES(`name`),
            `history_json` = VALUES(`history_json`),
            `history_hash` = VALUES(`history_hash`),
            `checked_on` = VALUES(`checked_on`),
            `checked_at` = VALUES(`checked_at`),
            `changed_at` = IF(VALUES(`changed_at`) IS NULL, `changed_at`, VALUES(`changed_at`)),
            `changed_report_date` = IF(VALUES(`changed_report_date`) IS NULL,
                                       `changed_report_date`,
                                       VALUES(`changed_report_date`))
    """,
               code,
               _history_name(history),
               json.dumps(history, ensure_ascii=False, default=_json_default),
               _history_hash(history),
               now.strftime("%Y-%m-%d"),
               now.strftime("%Y-%m-%d %H:%M:%S"),
               changed_at,
               changed_report_date)


def _is_in_changed_display_window(changed_at, changed_report_date, now):
    if changed_at is None:
        return False
    if isinstance(changed_at, str):
        changed_at = datetime.datetime.fromisoformat(changed_at)
    if changed_report_date is None:
        changed_report_date = _changed_report_date(changed_at)
    elif isinstance(changed_report_date, datetime.datetime):
        changed_report_date = changed_report_date.date()
    elif isinstance(changed_report_date, str):
        changed_report_date = datetime.date.fromisoformat(changed_report_date[:10])
    return changed_at <= now and now.date() <= changed_report_date


def _has_recent_dividend_notice(history, changed_at):
    if changed_at is None:
        return False
    if isinstance(changed_at, str):
        changed_at = datetime.datetime.fromisoformat(changed_at)
    notice_threshold = changed_at.date() - datetime.timedelta(days=1)
    for item in history or []:
        for field in ("NOTICE_DATE", "PLAN_NOTICE_DATE"):
            notice_date = _date_text(item.get(field))
            if len(notice_date) >= 10 and datetime.date.fromisoformat(notice_date[:10]) >= notice_threshold:
                return True
    return False


def _refresh_dividend_histories(stock_codes):
    global _DIVIDEND_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        for code in stock_codes:
            now = _now()
            cache_row, history = _read_dividend_history_cache(db, code)
            if not _is_dividend_history_cache_stale(cache_row, history, now):
                continue

            fresh_history = _fetch_dividend_history(code)
            old_hash = None
            if cache_row is not None:
                old_hash = cache_row.get("history_hash")
            fresh_hash = _history_hash(fresh_history)
            changed = (
                old_hash is not None
                and bool(history)
                and bool(fresh_history)
                and fresh_hash != old_hash
                and _has_recent_dividend_notice(fresh_history, now)
            )
            _write_dividend_history_cache(db, code, fresh_history, now, changed)
    except Exception as error:
        print(f"highDividendHandler._refresh_dividend_histories处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _DIVIDEND_REFRESH_LOCK:
            _DIVIDEND_REFRESH_RUNNING = False


def _schedule_dividend_history_refresh(stock_codes):
    global _DIVIDEND_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    with _DIVIDEND_REFRESH_LOCK:
        if _DIVIDEND_REFRESH_RUNNING:
            return
        _DIVIDEND_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_dividend_histories, args=(stock_codes,), daemon=True)
    thread.start()


def _get_cached_dividend_history(db, code):
    now = _now()
    cache_row, history = _read_dividend_history_cache(db, code)
    changed_at = None if cache_row is None else cache_row.get("changed_at")
    changed_report_date = None if cache_row is None else cache_row.get("changed_report_date")
    is_stale = _is_dividend_history_cache_stale(cache_row, history, now)
    dividend_changed = (
        _is_in_changed_display_window(changed_at, changed_report_date, now)
        and _has_recent_dividend_notice(history, changed_at)
    )
    return history, dividend_changed, is_stale


def _fetch_finance_report_history(code):
    _throttle_external_request()
    url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "ALL",
        "quoteColumns": "",
        "filter": f'(SECURITY_CODE="{code}")',
        "pageNumber": "1",
        "pageSize": "20",
        "sortTypes": "-1",
        "sortColumns": "REPORT_DATE",
        "source": "HSF10",
    }
    response = _DIVIDEND_FETCHER.make_request(url, params=params, timeout=15)
    payload = response.json()
    if not payload.get("success") or not payload.get("result"):
        return []
    return payload["result"].get("data") or []


def _finance_report_name(history):
    for item in history:
        name = item.get("SECURITY_NAME_ABBR")
        if name:
            return name
    return ""


def _read_finance_report_cache(db, code):
    row = db.get(f"""
        SELECT `code`, `name`, `finance_json`, `finance_hash`, `checked_on`, `checked_at`,
               `changed_at`, `changed_report_date`
        FROM `{_FINANCE_REPORT_CACHE_TABLE}`
        WHERE `code` = %s
    """, code)
    if row is None:
        return None, []
    try:
        return row, json.loads(row["finance_json"])
    except Exception:
        return row, []


def _is_finance_report_cache_stale(cache_row, history, now):
    return _is_daily_report_cache_stale(
        cache_row,
        now,
        after_close_interval_hours=_REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS
    )


def _latest_finance_report_deducted_growth(history):
    row = _latest_finance_report(history)
    if row is None:
        return None
    growth = _to_float(row.get("KCFJCXSYJLRTZ"))
    if growth is None:
        return None
    return (_date_text(row.get("REPORT_DATE")), growth)


def _write_finance_report_cache(db, code, history, now, changed):
    changed_at = now.strftime("%Y-%m-%d %H:%M:%S") if changed else None
    changed_report_date = _changed_report_date(now).strftime("%Y-%m-%d") if changed else None
    db.execute(f"""
        INSERT INTO `{_FINANCE_REPORT_CACHE_TABLE}`
            (`code`, `name`, `finance_json`, `finance_hash`, `checked_on`, `checked_at`,
             `changed_at`, `changed_report_date`)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `name` = VALUES(`name`),
            `finance_json` = VALUES(`finance_json`),
            `finance_hash` = VALUES(`finance_hash`),
            `checked_on` = VALUES(`checked_on`),
            `checked_at` = VALUES(`checked_at`),
            `changed_at` = IF(VALUES(`changed_at`) IS NULL, `changed_at`, VALUES(`changed_at`)),
            `changed_report_date` = IF(VALUES(`changed_report_date`) IS NULL,
                                       `changed_report_date`,
                                       VALUES(`changed_report_date`))
    """,
               code,
               _finance_report_name(history),
               json.dumps(history, ensure_ascii=False, default=_json_default),
               _history_hash(history),
               now.strftime("%Y-%m-%d"),
               now.strftime("%Y-%m-%d %H:%M:%S"),
               changed_at,
               changed_report_date)


def _refresh_finance_reports(stock_codes):
    global _FINANCE_REPORT_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        for code in stock_codes:
            now = _now()
            cache_row, history = _read_finance_report_cache(db, code)
            if not _is_finance_report_cache_stale(cache_row, history, now):
                continue

            fresh_history = _fetch_finance_report_history(code)
            old_growth = _latest_finance_report_deducted_growth(history)
            fresh_growth = _latest_finance_report_deducted_growth(fresh_history)
            changed = bool(history) and fresh_growth is not None and fresh_growth != old_growth
            _write_finance_report_cache(db, code, fresh_history, now, changed)
    except Exception as error:
        print(f"highDividendHandler._refresh_finance_reports处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _FINANCE_REPORT_REFRESH_LOCK:
            _FINANCE_REPORT_REFRESH_RUNNING = False


def _schedule_finance_report_refresh(stock_codes):
    global _FINANCE_REPORT_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    with _FINANCE_REPORT_REFRESH_LOCK:
        if _FINANCE_REPORT_REFRESH_RUNNING:
            return
        _FINANCE_REPORT_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_finance_reports, args=(stock_codes,), daemon=True)
    thread.start()


def _latest_finance_report(history):
    if not history:
        return None
    return sorted(history, key=lambda item: _date_text(item.get("REPORT_DATE")), reverse=True)[0]


def _latest_annual_diluted_eps(history):
    row = _latest_annual_report(history)
    if row is None:
        return {}
    eps = None
    eps_field = ""
    for field in ("EPSXS", "EPSJB", "EPSKCJB"):
        eps = _to_float(row.get(field))
        if eps is not None:
            eps_field = field
            break
    return {
        "diluted_eps": eps,
        "diluted_eps_field": eps_field,
        "diluted_eps_report_date": _date_text(row.get("REPORT_DATE")),
        "diluted_eps_report_name": row.get("REPORT_DATE_NAME") or row.get("REPORT_TYPE") or "",
    }


def _get_cached_latest_finance_report(db, code):
    now = _now()
    cache_row, history = _read_finance_report_cache(db, code)
    is_stale = _is_finance_report_cache_stale(cache_row, history, now)
    changed_at = None if cache_row is None else cache_row.get("changed_at")
    changed_report_date = None if cache_row is None else cache_row.get("changed_report_date")
    report_changed = _is_in_changed_display_window(changed_at, changed_report_date, now)
    annual_eps = _latest_annual_diluted_eps(history)
    row = _latest_finance_report(history)
    if row is None:
        annual_eps["report_changed"] = False
        return annual_eps, is_stale
    return {
        "deducted_profit_growth": _to_float(row.get("KCFJCXSYJLRTZ")),
        "report_date": _date_text(row.get("REPORT_DATE")),
        "report_name": row.get("REPORT_DATE_NAME") or row.get("REPORT_TYPE") or "",
        "notice_date": _date_text(row.get("NOTICE_DATE")),
        "deducted_profit": _to_float(row.get("KCFJCXSYJLR")),
        "report_changed": report_changed,
        **annual_eps,
    }, is_stale


def _fetch_cashflow_history(code):
    _throttle_external_request()
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_DMSK_FN_CASHFLOW",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")',
        "pageNumber": "1",
        "pageSize": "20",
        "sortTypes": "-1",
        "sortColumns": "REPORT_DATE",
        "source": "WEB",
        "client": "WEB",
    }
    response = _DIVIDEND_FETCHER.make_request(url, params=params, timeout=15)
    payload = response.json()
    if not payload.get("success") or not payload.get("result"):
        return []
    return payload["result"].get("data") or []


def _read_cashflow_cache(db, code):
    row = db.get(f"""
        SELECT `code`, `name`, `cashflow_json`, `checked_on`, `checked_at`
        FROM `{_CASHFLOW_CACHE_TABLE}`
        WHERE `code` = %s
    """, code)
    if row is None:
        return None, []
    try:
        return row, json.loads(row["cashflow_json"])
    except Exception:
        return row, []


def _is_cashflow_cache_stale(cache_row, history, now):
    if cache_row is None:
        return True
    checked_on = cache_row.get("checked_on")
    checked_at = cache_row.get("checked_at")
    if not checked_on or not checked_at:
        return True
    if isinstance(checked_on, datetime.datetime):
        checked_on = checked_on.date()

    if now.weekday() >= 5:
        return False
    if now.month <= 5:
        return _is_daily_report_cache_stale(cache_row, now, after_close_once=True)
    return checked_at <= now - datetime.timedelta(days=_CASHFLOW_OFFSEASON_REFRESH_DAYS)


def _write_cashflow_cache(db, code, history, now):
    db.execute(f"""
        INSERT INTO `{_CASHFLOW_CACHE_TABLE}`
            (`code`, `name`, `cashflow_json`, `checked_on`, `checked_at`)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `name` = VALUES(`name`),
            `cashflow_json` = VALUES(`cashflow_json`),
            `checked_on` = VALUES(`checked_on`),
            `checked_at` = VALUES(`checked_at`)
    """,
               code,
               _finance_report_name(history),
               json.dumps(history, ensure_ascii=False, default=_json_default),
               now.strftime("%Y-%m-%d"),
               now.strftime("%Y-%m-%d %H:%M:%S"))


def _refresh_cashflows(stock_codes):
    global _CASHFLOW_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        for code in stock_codes:
            now = _now()
            cache_row, history = _read_cashflow_cache(db, code)
            if not _is_cashflow_cache_stale(cache_row, history, now):
                continue

            fresh_history = _fetch_cashflow_history(code)
            _write_cashflow_cache(db, code, fresh_history, now)
    except Exception as error:
        print(f"highDividendHandler._refresh_cashflows处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _CASHFLOW_REFRESH_LOCK:
            _CASHFLOW_REFRESH_RUNNING = False


def _schedule_cashflow_refresh(stock_codes):
    global _CASHFLOW_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    with _CASHFLOW_REFRESH_LOCK:
        if _CASHFLOW_REFRESH_RUNNING:
            return
        _CASHFLOW_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_cashflows, args=(stock_codes,), daemon=True)
    thread.start()


def _is_annual_report_row(item):
    report_date = _date_text(item.get("REPORT_DATE"))
    return (
        report_date.endswith("-12-31")
        or item.get("DATE_TYPE_CODE") == "001"
        or "年报" in str(item.get("REPORT_TYPE") or item.get("REPORT_DATE_NAME") or "")
    )


def _latest_annual_report(history):
    annual_rows = [item for item in history or [] if _is_annual_report_row(item)]
    if not annual_rows:
        return None
    return sorted(annual_rows, key=lambda item: _date_text(item.get("REPORT_DATE")), reverse=True)[0]


def _is_financial_industry(*rows):
    financial_keywords = ("银行", "保险", "证券", "券商", "多元金融", "金融服务", "信托", "期货")
    for row in rows:
        if not row:
            continue
        text = " ".join(str(row.get(field) or "") for field in (
            "INDUSTRY_NAME",
            "BOARD_NAME",
            "PUBLISHNAME",
            "ORG_TYPE",
        ))
        if any(keyword in text for keyword in financial_keywords):
            return True
    return False


def _calculate_annual_narrow_fcf(finance_history, cashflow_history):
    cashflow_row = _latest_annual_report(cashflow_history)
    if cashflow_row is None:
        return {}

    report_date = _date_text(cashflow_row.get("REPORT_DATE"))
    finance_row = None
    for item in finance_history or []:
        if _is_annual_report_row(item) and _date_text(item.get("REPORT_DATE")) == report_date:
            finance_row = item
            break
    if finance_row is None:
        finance_row = _latest_annual_report(finance_history)

    if _is_financial_industry(cashflow_row, finance_row):
        return {
            "narrow_fcf_skipped": True,
            "narrow_fcf_skip_reason": "金融行业不适用窄口径FCF",
        }

    netcash_operate = _to_float(cashflow_row.get("NETCASH_OPERATE"))
    construct_long_asset = _to_float(cashflow_row.get("CONSTRUCT_LONG_ASSET"))
    total_share = None if finance_row is None else _to_float(finance_row.get("TOTAL_SHARE"))
    if netcash_operate is None or construct_long_asset is None or not total_share:
        return {}

    narrow_fcf_total = netcash_operate - construct_long_asset
    return {
        "narrow_fcf": narrow_fcf_total / total_share,
        "narrow_fcf_total": narrow_fcf_total,
        "narrow_fcf_report_date": report_date,
        "narrow_fcf_report_name": cashflow_row.get("REPORT_TYPE") or f"{report_date[:4]}年报",
        "netcash_operate": netcash_operate,
        "construct_long_asset": construct_long_asset,
        "total_share": total_share,
    }


def _calculate_financial_annual_eps(finance_history):
    finance_row = _latest_annual_report(finance_history)
    if finance_row is None:
        return {}

    eps = None
    eps_field = ""
    for field in ("EPSXS", "EPSJB", "EPSKCJB"):
        eps = _to_float(finance_row.get(field))
        if eps is not None:
            eps_field = field
            break
    if eps is None:
        return {}

    report_date = _date_text(finance_row.get("REPORT_DATE"))
    return {
        "narrow_fcf": eps,
        "narrow_fcf_report_date": report_date,
        "narrow_fcf_report_name": finance_row.get("REPORT_DATE_NAME") or finance_row.get("REPORT_TYPE") or f"{report_date[:4]}年报",
        "narrow_fcf_metric": "eps",
        "narrow_fcf_metric_name": "稀释每股收益",
        "eps_field": eps_field,
    }


def _get_cached_annual_narrow_fcf(db, code):
    now = _now()
    finance_cache_row, finance_history = _read_finance_report_cache(db, code)
    if not finance_history:
        return {}, False
    if _is_financial_industry(_latest_finance_report(finance_history), _latest_annual_report(finance_history)):
        return _calculate_financial_annual_eps(finance_history), False

    cashflow_cache_row, cashflow_history = _read_cashflow_cache(db, code)
    is_stale = _is_cashflow_cache_stale(cashflow_cache_row, cashflow_history, now)
    return _calculate_annual_narrow_fcf(finance_history, cashflow_history), is_stale


def _sum_fiscal_year_dividend(history, year):
    total_per_10 = 0.0
    details = []
    for item in history:
        report_date = _date_text(item.get("REPORT_DATE"))
        if not report_date.startswith(str(year)):
            continue

        cash_per_10 = _to_float(item.get("PRETAX_BONUS_RMB"))
        if cash_per_10 is None or cash_per_10 <= 0:
            continue

        total_per_10 += cash_per_10
        details.append({
            "name": item.get("SECURITY_NAME_ABBR") or "",
            "report_date": report_date,
            "cash_per_10": cash_per_10,
            "progress": item.get("ASSIGN_PROGRESS") or "",
            "plan": item.get("IMPL_PLAN_PROFILE") or "",
            "plan_notice_date": _date_text(item.get("PLAN_NOTICE_DATE")),
            "notice_date": _date_text(item.get("NOTICE_DATE")),
            "ex_dividend_date": _date_text(item.get("EX_DIVIDEND_DATE")),
        })

    return total_per_10, details


def _latest_dividend_year(history):
    """返回有派息的最近一个已完结财年。

    只认 -12-31 年报记录会把「只有年中派息、没有年报派息」的年度漏掉
    （如某年公司不分红或只做中期分红），从而错误回退到更早的年度；
    但当年（进行中）的中期派息也不算最新年度，否则会把未完的当年误当完整年度。
    """
    years = []
    for item in history:
        report_date = _date_text(item.get("REPORT_DATE"))
        if len(report_date) < 4:
            continue
        cash_per_10 = _to_float(item.get("PRETAX_BONUS_RMB"))
        if cash_per_10 is None or cash_per_10 <= 0:
            continue
        years.append(int(report_date[:4]))

    current_year = _now().year
    completed_years = [year for year in years if year < current_year]
    if completed_years:
        return max(completed_years)
    if years:
        return max(years)
    return current_year - 1


def _consecutive_non_decline_years(history, year):
    dividends_by_year = {}
    for item in history:
        report_date = _date_text(item.get("REPORT_DATE"))
        if len(report_date) < 4:
            continue
        cash_per_10 = _to_float(item.get("PRETAX_BONUS_RMB"))
        if cash_per_10 is None or cash_per_10 <= 0:
            continue
        fiscal_year = int(report_date[:4])
        dividends_by_year[fiscal_year] = dividends_by_year.get(fiscal_year, 0.0) + cash_per_10

    years = 0
    current_year = int(year)
    while current_year in dividends_by_year and current_year - 1 in dividends_by_year:
        if dividends_by_year[current_year] < dividends_by_year[current_year - 1]:
            break
        years += 1
        current_year -= 1
    return years


class HighDividendPageHandler(webBase.BaseHandler):
    def get(self):
        _ensure_cache_tables(self.db)
        self.render("high_dividend.html")


class HighDividendDataHandler(webBase.BaseHandler):
    def get(self):
        _ensure_cache_tables(self.db)
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        now = _now()
        stock_codes = [code for code in stocklist.get_stock_codes() if stocklist.is_a_stock_code(code)]
        total_stock_count = len(stock_codes)
        rows = []
        errors = []
        stock_names = stocklist.get_stock_names()

        price_by_code = _get_cached_price_rows(self.db, stock_codes, errors)
        profile_by_code = _get_cached_profile_rows(self.db, stock_codes)
        # 屏蔽 blocklist_industry.txt 中指定的申万二级行业，被屏蔽的股票不再读取缓存、不再刷新
        blocked_industries = stocklist.get_blocked_industries()
        if blocked_industries:
            stock_codes = [
                code for code in stock_codes
                if profile_by_code.get(code) is None
                or (profile_by_code[code].get("industry_name") or "") not in blocked_industries
            ]
        # 屏蔽 blocklist_dividendGrowthYearZero.txt 中记录的息增年为0的股票，被屏蔽的股票不再读取缓存、不再刷新
        blocked_zero_growth_codes = set(blocklist.get_blocked_codes(blocklist.GROWTH_YEAR_ZERO_FILE))
        if blocked_zero_growth_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_zero_growth_codes]
        # 屏蔽 blocklist_dividendYieldBelowOne.txt 中记录的股息率低于1%的股票，被屏蔽的股票不再读取缓存、不再刷新
        blocked_yield_below_one_codes = set(blocklist.get_blocked_codes(blocklist.YIELD_BELOW_ONE_FILE))
        if blocked_yield_below_one_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_yield_below_one_codes]
        # 屏蔽 blocklist_negativeEps.txt 中记录的收益（上年年报稀释每股收益）为负的股票，被屏蔽的股票不再读取缓存、不再刷新
        blocked_negative_eps_codes = set(blocklist.get_blocked_codes(blocklist.NEGATIVE_EPS_FILE))
        if blocked_negative_eps_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_negative_eps_codes]
        ma120_by_code = _read_ma120_cache(self.db, stock_codes)
        low20_by_code = _read_low20_cache(self.db, stock_codes)
        high20_by_code = _read_high20_cache(self.db, stock_codes)
        stale_ma120_codes = [
            code for code in stock_codes
            if _is_ma120_cache_stale(ma120_by_code.get(code), now)
        ]
        stale_low20_codes = [
            code for code in stock_codes
            if _is_low20_cache_stale(low20_by_code.get(code), now)
        ]
        stale_high20_codes = [
            code for code in stock_codes
            if _is_high20_cache_stale(high20_by_code.get(code), now)
        ]
        stale_profile_codes = [
            code for code in stock_codes
            if _is_profile_cache_stale(profile_by_code.get(code), now)
        ]
        stale_dividend_codes = []
        stale_finance_codes = []
        stale_cashflow_codes = []
        blocked_this_run_codes = set()

        for code in stock_codes:
            price_row = price_by_code.get(code)
            current_price = None if price_row is None else _to_float(price_row.get("current_price"))
            pre_close_price = None if price_row is None else _to_float(price_row.get("pre_close_price"))
            try:
                history, dividend_changed, dividend_history_stale = _get_cached_dividend_history(self.db, code)
                if dividend_history_stale:
                    stale_dividend_codes.append(code)
                dividend_year = _latest_dividend_year(history)
                dividend_per_10, details = _sum_fiscal_year_dividend(history, dividend_year)
                dividend_growth_years = _consecutive_non_decline_years(history, dividend_year)
                if dividend_growth_years == 0 and history:
                    # 息增年为0：自动记录到 blocklist_dividendGrowthYearZero.txt 并屏蔽，不再读取缓存、不再刷新
                    blocklist.add_blocked(blocklist.GROWTH_YEAR_ZERO_FILE, code, stock_names.get(code, ""))
                    blocked_this_run_codes.add(code)
                    continue
            except Exception as error:
                dividend_year = now.year - 1
                dividend_per_10 = 0.0
                details = []
                dividend_changed = False
                dividend_growth_years = 0
                errors.append(f"{code} 派息数据读取失败：{error}")

            try:
                finance_report, finance_report_stale = _get_cached_latest_finance_report(self.db, code)
                if finance_report_stale:
                    stale_finance_codes.append(code)
            except Exception as error:
                finance_report = {}
                errors.append(f"{code} 财报数据读取失败：{error}")

            # 收益（上年年报稀释每股收益）为负：自动记录到 blocklist_negativeEps.txt 并屏蔽，不再读取缓存、不再刷新
            if finance_report.get("diluted_eps") is not None and finance_report.get("diluted_eps") < 0:
                blocklist.add_blocked(blocklist.NEGATIVE_EPS_FILE, code, stock_names.get(code, ""))
                blocked_this_run_codes.add(code)
                continue

            try:
                annual_fcf, cashflow_stale = _get_cached_annual_narrow_fcf(self.db, code)
                if cashflow_stale:
                    stale_cashflow_codes.append(code)
            except Exception as error:
                annual_fcf = {}
                errors.append(f"{code} 现金流数据读取失败：{error}")

            dividend_per_share = dividend_per_10 / 10
            dividend_yield = None
            if current_price and current_price > 0:
                dividend_yield = dividend_per_share / current_price * 100
            if dividend_yield is not None and dividend_yield < 1:
                # 股息率低于1%：自动记录到 blocklist_dividendYieldBelowOne.txt 并屏蔽，不再读取缓存、不再刷新
                blocklist.add_blocked(blocklist.YIELD_BELOW_ONE_FILE, code, stock_names.get(code, ""))
                blocked_this_run_codes.add(code)
                continue
            ma120_row = ma120_by_code.get(code, {})
            ma120_position = None if not ma120_row else _to_float(ma120_row.get("ma120_position"))
            low20_row = low20_by_code.get(code, {})
            low20_bounce = None if not low20_row else _to_float(low20_row.get("bounce_position"))
            high20_row = high20_by_code.get(code, {})
            high20_decline = None if not high20_row else _to_float(high20_row.get("decline_position"))
            narrow_fcf = annual_fcf.get("narrow_fcf")
            fcf_dividend = None
            fcf_price = None
            if narrow_fcf is not None:
                if dividend_per_share > 0:
                    fcf_dividend = narrow_fcf / dividend_per_share
                if current_price and current_price > 0:
                    fcf_price = narrow_fcf / current_price * 100
            name = stock_names.get(code, "")

            rows.append({
                "code": code,
                "name": name,
                "deducted_profit_growth": finance_report.get("deducted_profit_growth"),
                "deducted_profit_growth_report_date": finance_report.get("report_date", ""),
                "deducted_profit_growth_report_name": finance_report.get("report_name", ""),
                "deducted_profit_growth_notice_date": finance_report.get("notice_date", ""),
                "deducted_profit": finance_report.get("deducted_profit"),
                "diluted_eps": finance_report.get("diluted_eps"),
                "diluted_eps_field": finance_report.get("diluted_eps_field", ""),
                "diluted_eps_report_date": finance_report.get("diluted_eps_report_date", ""),
                "diluted_eps_report_name": finance_report.get("diluted_eps_report_name", ""),
                "finance_report_changed": finance_report.get("report_changed", False),
                "industry_name": "" if profile_by_code.get(code) is None else (profile_by_code[code].get("industry_name") or ""),
                "narrow_fcf": narrow_fcf,
                "narrow_fcf_report_date": annual_fcf.get("narrow_fcf_report_date", ""),
                "narrow_fcf_report_name": annual_fcf.get("narrow_fcf_report_name", ""),
                "narrow_fcf_total": annual_fcf.get("narrow_fcf_total"),
                "narrow_fcf_metric": annual_fcf.get("narrow_fcf_metric", "fcf"),
                "narrow_fcf_metric_name": annual_fcf.get("narrow_fcf_metric_name", "窄口径FCF"),
                "netcash_operate": annual_fcf.get("netcash_operate"),
                "construct_long_asset": annual_fcf.get("construct_long_asset"),
                "total_share": annual_fcf.get("total_share"),
                "eps_field": annual_fcf.get("eps_field", ""),
                "narrow_fcf_skipped": annual_fcf.get("narrow_fcf_skipped", False),
                "narrow_fcf_skip_reason": annual_fcf.get("narrow_fcf_skip_reason", ""),
                "fcf_dividend": fcf_dividend,
                "fcf_price": fcf_price,
                "ma120_trade_date": "" if not ma120_row else _date_text(ma120_row.get("trade_date")),
                "ma120_close_price": None if not ma120_row else _to_float(ma120_row.get("close_price")),
                "ma120": None if not ma120_row else _to_float(ma120_row.get("ma120")),
                "ma120_position": ma120_position,
                "ma120_signal": _ma120_trade_signal(
                    current_price,
                    pre_close_price,
                    ma120_position,
                    None if not ma120_row else _to_float(ma120_row.get("ma120"))),
                "low20_trade_date": "" if not low20_row else _date_text(low20_row.get("trade_date")),
                "low20_close_price": None if not low20_row else _to_float(low20_row.get("close_price")),
                "low20_lowest_date": "" if not low20_row else _date_text(low20_row.get("lowest_date")),
                "low20_lowest_low": None if not low20_row else _to_float(low20_row.get("lowest_low")),
                "low20_bounce": low20_bounce,
                "high20_trade_date": "" if not high20_row else _date_text(high20_row.get("trade_date")),
                "high20_close_price": None if not high20_row else _to_float(high20_row.get("close_price")),
                "high20_highest_date": "" if not high20_row else _date_text(high20_row.get("highest_date")),
                "high20_highest_high": None if not high20_row else _to_float(high20_row.get("highest_high")),
                "high20_decline": high20_decline,
                "price_date": "" if price_row is None else _date_text(price_row.get("price_date")),
                "current_price": current_price,
                "market_cap": None if profile_by_code.get(code) is None else _to_float(profile_by_code[code].get("market_cap")),
                "dividend_year": dividend_year,
                "dividend_per_10": round(dividend_per_10, 4),
                "dividend_per_share": round(dividend_per_share, 4),
                "dividend_yield": dividend_yield,
                "dividend_growth_years": dividend_growth_years,
                "dividend_changed": dividend_changed,
                "details": details,
            })

        # 本次新屏蔽的股票不再安排任何缓存刷新
        if blocked_this_run_codes:
            for stale_list in (
                stale_ma120_codes, stale_low20_codes, stale_high20_codes,
                stale_profile_codes, stale_finance_codes, stale_dividend_codes,
            ):
                stale_list[:] = [code for code in stale_list if code not in blocked_this_run_codes]

        _schedule_ma120_refresh(stale_ma120_codes)
        _schedule_low20_refresh(stale_low20_codes)
        _schedule_high20_refresh(stale_high20_codes)
        _schedule_profile_refresh(stale_profile_codes)
        _schedule_dividend_history_refresh(stale_dividend_codes)
        _schedule_finance_report_refresh(stale_finance_codes)
        _schedule_cashflow_refresh(stale_cashflow_codes)
        rows.sort(key=lambda item: (item["dividend_yield"] is not None, item["dividend_yield"] or 0), reverse=True)
        payload = {
            "total_stock_count": total_stock_count,
            "stock_count": len(rows),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "cache_policy": {
                "price": "盘中最多每30分钟刷新一次，盘后保持收盘价；外部请求间隔至少2秒",
                "profile": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日数据；外部请求间隔至少2秒",
                "ma120": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据；外部请求间隔至少2秒",
                "low20": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据；外部请求间隔至少2秒",
                "high20": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据；外部请求间隔至少2秒",
                "dividend_history": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次；外部请求间隔至少2秒",
                "finance_report": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次；外部请求间隔至少2秒",
                "cashflow": "页面请求只读缓存；窄口径FCF只取最新年报，金融行业不抓取；年报季交易日检查，非年报季最多7天一次；外部请求间隔至少2秒",
            },
            "errors": errors,
            "data": rows,
        }
        self.write(json.dumps(payload, ensure_ascii=False, default=_json_default))


class FollowListHandler(webBase.BaseHandler):
    def get(self):
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        toggle_code = self.get_argument("toggle", "", True)
        if toggle_code:
            now_followed = followlist.toggle_follow(toggle_code)
            self.write(json.dumps({
                "code": toggle_code,
                "followed": now_followed,
            }, ensure_ascii=False))
            return

        codes = followlist.get_follow_codes()
        self.write(json.dumps({
            "follow_codes": codes,
        }, ensure_ascii=False))


