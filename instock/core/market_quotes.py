#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.core.stocklist as stocklist
from instock.core.common import (
    _to_float,
    _now,
    _market_phase,
    _previous_trading_day,
    _ensure_cache_tables,
    _PRICE_CACHE_TABLE,
    _MA120_CACHE_TABLE,
    _LOW20_CACHE_TABLE,
    _HIGH20_CACHE_TABLE,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

_PRICE_REFRESH_MINUTES = 30
_MA120_REFRESH_LOCK = threading.Lock()
_MA120_REFRESH_RUNNING = False
_MA120_REFRESH_ATTEMPTS = {}
_LOW20_REFRESH_LOCK = threading.Lock()
_LOW20_REFRESH_RUNNING = False
_LOW20_REFRESH_ATTEMPTS = {}
_HIGH20_REFRESH_LOCK = threading.Lock()
_HIGH20_REFRESH_RUNNING = False
_HIGH20_REFRESH_ATTEMPTS = {}


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
        print(f"market_quotes._refresh_ma120_positions处理异常：{error}")
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
    return thread


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
        print(f"market_quotes._refresh_low20_positions处理异常：{error}")
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
    return thread


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
        print(f"market_quotes._refresh_high20_positions处理异常：{error}")
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
    return thread
