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
import instock.web.base as webBase

__author__ = 'myh '
__date__ = '2026/5/12 '

_DIVIDEND_FETCHER = eastmoney_fetcher()
_CACHE_TABLE_READY = False
_CACHE_TABLE_LOCK = threading.Lock()
_EXTERNAL_REQUEST_LOCK = threading.Lock()
_DIVIDEND_REFRESH_LOCK = threading.Lock()
_MA120_REFRESH_LOCK = threading.Lock()
_DIVIDEND_REFRESH_RUNNING = False
_MA120_REFRESH_RUNNING = False
_MA120_REFRESH_ATTEMPTS = {}
_LAST_EXTERNAL_REQUEST_AT = 0.0
_EXTERNAL_REQUEST_INTERVAL_SECONDS = 2
_PRICE_CACHE_TABLE = "cn_high_dividend_price_cache"
_DIVIDEND_HISTORY_CACHE_TABLE = "cn_high_dividend_dividend_history_cache"
_FINANCE_REPORT_CACHE_TABLE = "cn_high_dividend_finance_report_cache"
_CASHFLOW_CACHE_TABLE = "cn_high_dividend_cashflow_cache"
_POSITION_CACHE_TABLE = "cn_high_dividend_position_cache"
_MA120_CACHE_TABLE = "cn_high_dividend_ma120_cache"
_VALUE_BET_RATE_SNAPSHOT_TABLE = "cn_high_dividend_value_bet_rate_snapshot"
_HIGH_DIVIDEND_LIST_SNAPSHOT_TABLE = "cn_high_dividend_list_snapshot"
_FINANCE_REPORT_REFRESH_LOCK = threading.Lock()
_CASHFLOW_REFRESH_LOCK = threading.Lock()
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_PRICE_REFRESH_MINUTES = 30
_DIVIDEND_REFRESH_HOUR = 8
_DIVIDEND_AFTER_CLOSE_REFRESH_START_HOUR = 16
_DIVIDEND_AFTER_CLOSE_REFRESH_END_HOUR = 23
_REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS = 4
_CASHFLOW_OFFSEASON_REFRESH_DAYS = 7
_HIGH_DIVIDEND_SNAPSHOT_HOUR = 16
_HIGH_DIVIDEND_SNAPSHOT_RETENTION_DAYS = 400
_FINANCE_REPORT_REFRESH_RUNNING = False
_CASHFLOW_REFRESH_RUNNING = False


def _to_float(value):
    try:
        if value in ("", None, "--", "-"):
            return None
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    except Exception:
        return None


def _calculate_value_bet_rate(dividend_yield, fcf_dividend, fcf_price, ma120_position):
    if any(value is None for value in (dividend_yield, fcf_dividend, fcf_price, ma120_position)):
        return None

    if dividend_yield < 3:
        dividend_score = (dividend_yield - 4) * 1.5
    else:
        dividend_score = min(dividend_yield, 6) - 4

    if fcf_dividend >= 1:
        fcf_dividend_score = 0
    elif fcf_dividend >= 0:
        fcf_dividend_score = (fcf_dividend - 1) * 6
    else:
        fcf_dividend_score = -10

    fcf_price_score = fcf_price - 4
    ma120_score = max(-10, min(10, -ma120_position * 0.25))
    return dividend_score + fcf_dividend_score + fcf_price_score + ma120_score


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
            CREATE TABLE IF NOT EXISTS `{_POSITION_CACHE_TABLE}` (
                `code` varchar(6) NOT NULL,
                `position_text` varchar(100) DEFAULT NULL,
                `narrow_fcf` decimal(12,4) DEFAULT NULL,
                `narrow_fcf_report_date` date DEFAULT NULL,
                `updated_at` datetime NOT NULL,
                PRIMARY KEY (`code`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"ALTER TABLE `{_POSITION_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `narrow_fcf` decimal(12,4) DEFAULT NULL")
        db.execute(f"ALTER TABLE `{_POSITION_CACHE_TABLE}` ADD COLUMN IF NOT EXISTS `narrow_fcf_report_date` date DEFAULT NULL")
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
            CREATE TABLE IF NOT EXISTS `{_VALUE_BET_RATE_SNAPSHOT_TABLE}` (
                `code` varchar(6) NOT NULL,
                `snapshot_date` date NOT NULL,
                `value_bet_rate` decimal(12,4) DEFAULT NULL,
                `updated_at` datetime NOT NULL,
                PRIMARY KEY (`code`, `snapshot_date`),
                INDEX `idx_snapshot_date` (`snapshot_date`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
        db.execute(f"""
            CREATE TABLE IF NOT EXISTS `{_HIGH_DIVIDEND_LIST_SNAPSHOT_TABLE}` (
                `snapshot_date` date NOT NULL,
                `stock_count` int DEFAULT NULL,
                `payload_json` longtext NOT NULL,
                `generated_at` datetime NOT NULL,
                `created_at` datetime NOT NULL,
                PRIMARY KEY (`snapshot_date`),
                INDEX `idx_generated_at` (`generated_at`)
            ) CHARACTER SET = utf8mb4 COLLATE = utf8mb4_general_ci ROW_FORMAT = Dynamic;
        """)
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
        SELECT `code`, `name`, `price_date`, `current_price`, `fetched_at`, `market_phase`
        FROM `{_PRICE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)


def _write_price_cache(db, price_data, now):
    phase = _market_phase(now)
    for row in price_data:
        current_price = _to_float(row.get("new_price"))
        if current_price is None or current_price <= 0:
            current_price = _to_float(row.get("pre_close_price"))
        db.execute(f"""
            INSERT INTO `{_PRICE_CACHE_TABLE}`
                (`code`, `name`, `price_date`, `current_price`, `fetched_at`, `market_phase`)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `name` = VALUES(`name`),
                `price_date` = VALUES(`price_date`),
                `current_price` = VALUES(`current_price`),
                `fetched_at` = VALUES(`fetched_at`),
                `market_phase` = VALUES(`market_phase`)
        """,
                   row.get("code"),
                   row.get("name"),
                   row.get("date"),
                   current_price,
                   now.strftime("%Y-%m-%d %H:%M:%S"),
                   phase)


def _get_cached_price_rows(db, stock_codes, errors):
    now = _now()
    cached_rows = _read_price_cache(db, stock_codes)
    if _is_price_cache_stale(cached_rows, stock_codes, now):
        try:
            _throttle_external_request()
            price_data = stocklist.make_selected_stock_rows(now.date())
            if price_data is not None:
                _write_price_cache(db, price_data, now)
                cached_rows = _read_price_cache(db, stock_codes)
        except Exception as error:
            errors.append(f"行情缓存刷新失败，已使用旧缓存：{error}")
    return {row["code"]: row for row in cached_rows}


def _read_manual_cache(db, stock_codes):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT `code`, `position_text`, `narrow_fcf`, `narrow_fcf_report_date`
        FROM `{_POSITION_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)
    return {
        row["code"]: {
            "position": row.get("position_text") or "",
            "narrow_fcf": _to_float(row.get("narrow_fcf")),
            "narrow_fcf_report_date": _date_text(row.get("narrow_fcf_report_date")),
        }
        for row in rows
    }


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


def _is_ma120_cache_stale(cache_row, now):
    phase = _market_phase(now)
    if phase == "intraday":
        return False
    if cache_row is None:
        return True
    fetched_at = cache_row.get("fetched_at")
    if not fetched_at:
        return True
    if phase == "after_close":
        close_time = datetime.datetime.combine(now.date(), datetime.time(15, 0))
        return fetched_at < close_time
    return fetched_at.date() < now.date()


def _ma120_refresh_window(now):
    phase = _market_phase(now)
    if phase == "intraday":
        return None
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
        for code in stock_codes:
            now = _now()
            cache_row = _read_ma120_cache(db, [code]).get(code)
            if not _is_ma120_cache_stale(cache_row, now):
                continue

            _throttle_external_request()
            ma120_row = stocklist.fetch_daily_ma120_position(code, today=now.date())
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


def _write_position_cache(db, code, position_text):
    db.execute(f"""
        INSERT INTO `{_POSITION_CACHE_TABLE}` (`code`, `position_text`, `updated_at`)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `position_text` = VALUES(`position_text`),
            `updated_at` = VALUES(`updated_at`)
    """,
               code,
               position_text,
               _now().strftime("%Y-%m-%d %H:%M:%S"))


def _write_fcf_cache(db, code, narrow_fcf, report_date):
    db.execute(f"""
        INSERT INTO `{_POSITION_CACHE_TABLE}`
            (`code`, `narrow_fcf`, `narrow_fcf_report_date`, `updated_at`)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `narrow_fcf` = VALUES(`narrow_fcf`),
            `narrow_fcf_report_date` = VALUES(`narrow_fcf_report_date`),
            `updated_at` = VALUES(`updated_at`)
    """,
               code,
               narrow_fcf,
               report_date if narrow_fcf is not None else None,
               _now().strftime("%Y-%m-%d %H:%M:%S"))


def _read_value_bet_rate_snapshots(db, stock_codes, today, periods=(1, 7, 21)):
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    snapshots = {}
    for period in periods:
        target_date = today - datetime.timedelta(days=period)
        rows = db.query(f"""
            SELECT `code`, `snapshot_date`, `value_bet_rate`
            FROM `{_VALUE_BET_RATE_SNAPSHOT_TABLE}`
            WHERE `code` IN ({placeholders})
              AND `snapshot_date` <= %s
            ORDER BY `code`, `snapshot_date` DESC
        """, *(list(stock_codes) + [target_date.strftime("%Y-%m-%d")]))
        for row in rows:
            code = row.get("code")
            code_snapshots = snapshots.setdefault(code, {})
            if period not in code_snapshots:
                code_snapshots[period] = {
                    "snapshot_date": _date_text(row.get("snapshot_date")),
                    "value_bet_rate": _to_float(row.get("value_bet_rate")),
                }
    return snapshots


def _write_value_bet_rate_snapshots(db, rows, now):
    if now.weekday() >= 5 or _market_phase(now) != "after_close":
        return
    snapshot_date = now.strftime("%Y-%m-%d")
    updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        value_bet_rate = _to_float(row.get("value_bet_rate"))
        if value_bet_rate is None:
            continue
        db.execute(f"""
            INSERT INTO `{_VALUE_BET_RATE_SNAPSHOT_TABLE}`
                (`code`, `snapshot_date`, `value_bet_rate`, `updated_at`)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `value_bet_rate` = VALUES(`value_bet_rate`),
                `updated_at` = VALUES(`updated_at`)
        """,
                   row.get("code"),
                   snapshot_date,
                   value_bet_rate,
                   updated_at)


def _read_high_dividend_snapshot_dates(db):
    rows = db.query(f"""
        SELECT `snapshot_date`
        FROM `{_HIGH_DIVIDEND_LIST_SNAPSHOT_TABLE}`
        ORDER BY `snapshot_date` DESC
        LIMIT %s
    """, _HIGH_DIVIDEND_SNAPSHOT_RETENTION_DAYS)
    return [_date_text(row.get("snapshot_date")) for row in rows]


def _read_high_dividend_list_snapshot(db, snapshot_date):
    row = db.get(f"""
        SELECT `snapshot_date`, `payload_json`, `generated_at`
        FROM `{_HIGH_DIVIDEND_LIST_SNAPSHOT_TABLE}`
        WHERE `snapshot_date` = %s
    """, snapshot_date)
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except Exception:
        return None
    payload["is_snapshot"] = True
    payload["snapshot_date"] = _date_text(row.get("snapshot_date"))
    payload["generated_at"] = str(row.get("generated_at"))[:19]
    return payload


def _write_high_dividend_list_snapshot(db, payload, now):
    if now.time() < datetime.time(_HIGH_DIVIDEND_SNAPSHOT_HOUR, 0):
        return

    snapshot_date = _resolve_high_dividend_snapshot_date(payload, now)
    if not snapshot_date:
        return
    if snapshot_date != now.strftime("%Y-%m-%d") and _high_dividend_snapshot_exists(db, snapshot_date):
        return

    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")
    snapshot_payload = dict(payload)
    snapshot_payload.pop("snapshot_dates", None)
    snapshot_payload["is_snapshot"] = True
    snapshot_payload["snapshot_date"] = snapshot_date
    snapshot_payload["generated_at"] = generated_at

    db.execute(f"""
        INSERT INTO `{_HIGH_DIVIDEND_LIST_SNAPSHOT_TABLE}`
            (`snapshot_date`, `stock_count`, `payload_json`, `generated_at`, `created_at`)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `stock_count` = VALUES(`stock_count`),
            `payload_json` = VALUES(`payload_json`),
            `generated_at` = VALUES(`generated_at`)
    """,
               snapshot_date,
               payload.get("stock_count"),
               json.dumps(snapshot_payload, ensure_ascii=False, default=_json_default),
               generated_at,
               generated_at)

    cutoff_date = datetime.datetime.strptime(snapshot_date, "%Y-%m-%d").date() - datetime.timedelta(
        days=_HIGH_DIVIDEND_SNAPSHOT_RETENTION_DAYS - 1
    )
    db.execute(f"""
        DELETE FROM `{_HIGH_DIVIDEND_LIST_SNAPSHOT_TABLE}`
        WHERE `snapshot_date` < %s
    """, cutoff_date.strftime("%Y-%m-%d"))


def _resolve_high_dividend_snapshot_date(payload, now):
    price_dates = []
    for row in payload.get("data", []):
        price_date = _date_text(row.get("price_date"))
        if not price_date:
            continue
        try:
            parsed_date = datetime.datetime.strptime(price_date, "%Y-%m-%d").date()
        except Exception:
            continue
        if parsed_date <= now.date():
            price_dates.append(parsed_date)
    if price_dates:
        return max(price_dates).strftime("%Y-%m-%d")

    fallback_date = now.date()
    while fallback_date.weekday() >= 5:
        fallback_date -= datetime.timedelta(days=1)
    return fallback_date.strftime("%Y-%m-%d")


def _high_dividend_snapshot_exists(db, snapshot_date):
    row = db.get(f"""
        SELECT `snapshot_date`
        FROM `{_HIGH_DIVIDEND_LIST_SNAPSHOT_TABLE}`
        WHERE `snapshot_date` = %s
    """, snapshot_date)
    return row is not None


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


def _is_manual_narrow_fcf_stale(manual_narrow_fcf, manual_report_date, current_report_date):
    if manual_narrow_fcf is None:
        return False
    manual_report_date = _date_text(manual_report_date)
    current_report_date = _date_text(current_report_date)
    if not manual_report_date or not current_report_date:
        return False
    return current_report_date > manual_report_date


def _is_same_narrow_fcf_value(left, right):
    if left is None or right is None:
        return False
    try:
        return Decimal(str(left)).quantize(Decimal("0.01")) == Decimal(str(right)).quantize(Decimal("0.01"))
    except Exception:
        return False


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
    annual_years = []
    years = []
    for item in history:
        report_date = _date_text(item.get("REPORT_DATE"))
        if len(report_date) < 4:
            continue
        cash_per_10 = _to_float(item.get("PRETAX_BONUS_RMB"))
        if cash_per_10 is None or cash_per_10 <= 0:
            continue
        year = int(report_date[:4])
        years.append(year)
        if report_date.endswith("-12-31"):
            annual_years.append(year)
    if annual_years:
        return max(annual_years)
    if years:
        return max(years)
    else:
        return _now().year - 1


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
        snapshot_date = self.get_argument("snapshot_date", default="", strip=True)
        if snapshot_date:
            snapshot_date = _date_text(snapshot_date)
            snapshot_payload = _read_high_dividend_list_snapshot(self.db, snapshot_date)
            if snapshot_payload is None:
                self.set_status(404)
                self.write(json.dumps({
                    "success": False,
                    "message": "未找到该日期快照",
                    "snapshot_date": snapshot_date,
                    "snapshot_dates": _read_high_dividend_snapshot_dates(self.db),
                    "data": [],
                    "errors": [],
                }, ensure_ascii=False))
                return
            snapshot_payload["snapshot_dates"] = _read_high_dividend_snapshot_dates(self.db)
            self.write(json.dumps(snapshot_payload, ensure_ascii=False, default=_json_default))
            return

        stock_codes = [code for code in stocklist.get_stock_codes() if stocklist.is_a_stock_code(code)]
        rows = []
        errors = []

        price_by_code = _get_cached_price_rows(self.db, stock_codes, errors)
        manual_by_code = _read_manual_cache(self.db, stock_codes)
        ma120_by_code = _read_ma120_cache(self.db, stock_codes)
        value_bet_rate_snapshots = _read_value_bet_rate_snapshots(self.db, stock_codes, now.date())
        stale_ma120_codes = [
            code for code in stock_codes
            if _is_ma120_cache_stale(ma120_by_code.get(code), now)
        ]
        stale_dividend_codes = []
        stale_finance_codes = []
        stale_cashflow_codes = []

        for code in stock_codes:
            price_row = price_by_code.get(code)
            current_price = None if price_row is None else _to_float(price_row.get("current_price"))
            try:
                history, dividend_changed, dividend_history_stale = _get_cached_dividend_history(self.db, code)
                if dividend_history_stale:
                    stale_dividend_codes.append(code)
                dividend_year = _latest_dividend_year(history)
                dividend_per_10, details = _sum_fiscal_year_dividend(history, dividend_year)
                dividend_growth_years = _consecutive_non_decline_years(history, dividend_year)
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
            manual_row = manual_by_code.get(code, {})
            ma120_row = ma120_by_code.get(code, {})
            ma120_position = None if not ma120_row else _to_float(ma120_row.get("ma120_position"))
            default_narrow_fcf = annual_fcf.get("narrow_fcf")
            manual_narrow_fcf = manual_row.get("narrow_fcf")
            manual_narrow_fcf_report_date = manual_row.get("narrow_fcf_report_date", "")
            manual_narrow_fcf_stale = _is_manual_narrow_fcf_stale(
                manual_narrow_fcf,
                manual_narrow_fcf_report_date,
                annual_fcf.get("narrow_fcf_report_date", "")
            )
            narrow_fcf = manual_narrow_fcf if manual_narrow_fcf is not None else default_narrow_fcf
            narrow_fcf_source = "manual" if manual_narrow_fcf is not None else "default"
            fcf_dividend = None
            fcf_price = None
            if narrow_fcf is not None:
                if dividend_per_share > 0:
                    fcf_dividend = narrow_fcf / dividend_per_share
                if current_price and current_price > 0:
                    fcf_price = narrow_fcf / current_price * 100
            value_bet_rate = _calculate_value_bet_rate(
                dividend_yield,
                fcf_dividend,
                fcf_price,
                ma120_position
            )
            value_bet_rate_snapshot_rows = value_bet_rate_snapshots.get(code, {})
            value_bet_rate_changes = {}
            for period in (1, 7, 21):
                snapshot_row = value_bet_rate_snapshot_rows.get(period, {})
                previous_value_bet_rate = snapshot_row.get("value_bet_rate")
                value_bet_rate_change = None
                if value_bet_rate is not None and previous_value_bet_rate is not None:
                    value_bet_rate_change = value_bet_rate - previous_value_bet_rate
                value_bet_rate_changes[period] = {
                    "previous_value_bet_rate": previous_value_bet_rate,
                    "previous_value_bet_rate_date": snapshot_row.get("snapshot_date", ""),
                    "value_bet_rate_change": value_bet_rate_change,
                }

            name = "" if price_row is None else price_row.get("name")
            if not name and details:
                name = details[0].get("name", "")

            rows.append({
                "code": code,
                "name": name,
                "position": manual_row.get("position", ""),
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
                "narrow_fcf": narrow_fcf,
                "manual_narrow_fcf": manual_narrow_fcf,
                "manual_narrow_fcf_report_date": manual_narrow_fcf_report_date,
                "manual_narrow_fcf_stale": manual_narrow_fcf_stale,
                "default_narrow_fcf": default_narrow_fcf,
                "narrow_fcf_source": narrow_fcf_source,
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
                "value_bet_rate": value_bet_rate,
                "previous_value_bet_rate_1": value_bet_rate_changes[1]["previous_value_bet_rate"],
                "previous_value_bet_rate_date_1": value_bet_rate_changes[1]["previous_value_bet_rate_date"],
                "value_bet_rate_change_1": value_bet_rate_changes[1]["value_bet_rate_change"],
                "previous_value_bet_rate_7": value_bet_rate_changes[7]["previous_value_bet_rate"],
                "previous_value_bet_rate_date_7": value_bet_rate_changes[7]["previous_value_bet_rate_date"],
                "value_bet_rate_change_7": value_bet_rate_changes[7]["value_bet_rate_change"],
                "previous_value_bet_rate_21": value_bet_rate_changes[21]["previous_value_bet_rate"],
                "previous_value_bet_rate_date_21": value_bet_rate_changes[21]["previous_value_bet_rate_date"],
                "value_bet_rate_change_21": value_bet_rate_changes[21]["value_bet_rate_change"],
                "price_date": "" if price_row is None else _date_text(price_row.get("price_date")),
                "current_price": current_price,
                "dividend_year": dividend_year,
                "dividend_per_10": round(dividend_per_10, 4),
                "dividend_per_share": round(dividend_per_share, 4),
                "dividend_yield": dividend_yield,
                "dividend_growth_years": dividend_growth_years,
                "dividend_changed": dividend_changed,
                "details": details,
            })

        _schedule_ma120_refresh(stale_ma120_codes)
        _schedule_dividend_history_refresh(stale_dividend_codes)
        _schedule_finance_report_refresh(stale_finance_codes)
        _schedule_cashflow_refresh(stale_cashflow_codes)
        _write_value_bet_rate_snapshots(self.db, rows, now)
        rows.sort(key=lambda item: (item["dividend_yield"] is not None, item["dividend_yield"] or 0), reverse=True)
        payload = {
            "stock_count": len(stock_codes),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "is_snapshot": False,
            "snapshot_date": "",
            "cache_policy": {
                "price": "盘中最多每30分钟刷新一次，盘后保持收盘价；外部请求间隔至少2秒",
                "ma120": "页面请求只读缓存；盘中不刷新，收盘后或次日首次打开时后台刷新前一完整交易日收盘价对应的日MA120位置；外部请求间隔至少2秒",
                "dividend_history": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次；外部请求间隔至少2秒",
                "finance_report": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次；外部请求间隔至少2秒",
                "cashflow": "页面请求只读缓存；窄口径FCF只取最新年报，金融行业不抓取；年报季交易日检查，非年报季最多7天一次；外部请求间隔至少2秒",
            },
            "errors": errors,
            "data": rows,
        }
        _write_high_dividend_list_snapshot(self.db, payload, now)
        payload["snapshot_dates"] = _read_high_dividend_snapshot_dates(self.db)
        self.write(json.dumps(payload, ensure_ascii=False, default=_json_default))


class HighDividendPositionHandler(webBase.BaseHandler):
    def post(self):
        _ensure_cache_tables(self.db)
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        code = self.get_argument("code", default="", strip=True)
        position = self.get_argument("position", default="", strip=True)
        if not stocklist.is_a_stock_code(code):
            self.set_status(400)
            self.write(json.dumps({"success": False, "message": "股票代码无效"}, ensure_ascii=False))
            return

        _write_position_cache(self.db, code, position[:100])
        self.write(json.dumps({
            "success": True,
            "code": code,
            "position": position[:100],
            "updated_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False))


class HighDividendFcfHandler(webBase.BaseHandler):
    def post(self):
        _ensure_cache_tables(self.db)
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        code = self.get_argument("code", default="", strip=True)
        fcf_text = self.get_argument("narrow_fcf", default="", strip=True)
        if not stocklist.is_a_stock_code(code):
            self.set_status(400)
            self.write(json.dumps({"success": False, "message": "股票代码无效"}, ensure_ascii=False))
            return

        manual_narrow_fcf = None if fcf_text == "" else _to_float(fcf_text)
        if fcf_text != "" and manual_narrow_fcf is None:
            self.set_status(400)
            self.write(json.dumps({"success": False, "message": "FCF格式无效"}, ensure_ascii=False))
            return

        annual_fcf, _ = _get_cached_annual_narrow_fcf(self.db, code)
        current_report_date = annual_fcf.get("narrow_fcf_report_date", "")
        default_narrow_fcf = annual_fcf.get("narrow_fcf")
        if manual_narrow_fcf is not None and _is_same_narrow_fcf_value(manual_narrow_fcf, default_narrow_fcf):
            self.set_status(409)
            self.write(json.dumps({
                "success": False,
                "message": "手动值与抓取默认值相同，无需覆盖",
            }, ensure_ascii=False))
            return

        _write_fcf_cache(self.db, code, manual_narrow_fcf, current_report_date)
        price_row = _read_price_cache(self.db, [code])
        current_price = None if not price_row else _to_float(price_row[0].get("current_price"))
        _, dividend_history = _read_dividend_history_cache(self.db, code)
        dividend_year = _latest_dividend_year(dividend_history)
        dividend_per_10, _ = _sum_fiscal_year_dividend(dividend_history, dividend_year)
        dividend_per_share = dividend_per_10 / 10
        effective_narrow_fcf = manual_narrow_fcf if manual_narrow_fcf is not None else default_narrow_fcf
        manual_report_date = current_report_date if manual_narrow_fcf is not None else ""
        manual_narrow_fcf_stale = _is_manual_narrow_fcf_stale(
            manual_narrow_fcf,
            manual_report_date,
            current_report_date
        )
        fcf_dividend = None
        fcf_price = None
        if effective_narrow_fcf is not None:
            if dividend_per_share > 0:
                fcf_dividend = effective_narrow_fcf / dividend_per_share
            if current_price and current_price > 0:
                fcf_price = effective_narrow_fcf / current_price * 100

        self.write(json.dumps({
            "success": True,
            "code": code,
            "narrow_fcf": effective_narrow_fcf,
            "manual_narrow_fcf": manual_narrow_fcf,
            "manual_narrow_fcf_report_date": manual_report_date,
            "manual_narrow_fcf_stale": manual_narrow_fcf_stale,
            "default_narrow_fcf": default_narrow_fcf,
            "narrow_fcf_source": "manual" if manual_narrow_fcf is not None else "default",
            "fcf_dividend": fcf_dividend,
            "fcf_price": fcf_price,
            "updated_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False))
