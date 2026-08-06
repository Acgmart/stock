#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.core.stocklist as stocklist
import instock.core.dividend as dividend
from instock.core.common import (
    _to_float,
    _now,
    _date_text,
    _market_phase,
    _previous_trading_day,
    _ensure_cache_tables,
    _PRICE_CACHE_TABLE,
    _MA120_CACHE_TABLE,
    _LOW20_CACHE_TABLE,
    _HIGH20_CACHE_TABLE,
    _KLINE_CACHE_TABLE,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

_PRICE_REFRESH_MINUTES = 5
_KLINE_REFRESH_LOCK = threading.Lock()
_KLINE_REFRESH_RUNNING = False
_KLINE_REFRESH_ATTEMPTS = {}


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
        SELECT `code`, `name`, `price_date`, `current_price`, `pre_close_price`, `change_rate`, `fetched_at`, `market_phase`
        FROM `{_PRICE_CACHE_TABLE}`
        WHERE `code` IN ({placeholders})
    """, *stock_codes)


def _write_price_cache(db, price_data, now):
    phase = _market_phase(now)
    for row in price_data:
        current_price = _to_float(row.get("new_price"))
        pre_close_price = _to_float(row.get("pre_close_price"))
        change_rate = _to_float(row.get("change_rate"))
        if current_price is None or current_price <= 0:
            current_price = pre_close_price
        db.execute(f"""
            INSERT INTO `{_PRICE_CACHE_TABLE}`
                (`code`, `name`, `price_date`, `current_price`, `pre_close_price`, `change_rate`, `fetched_at`, `market_phase`)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                `name` = VALUES(`name`),
                `price_date` = VALUES(`price_date`),
                `current_price` = VALUES(`current_price`),
                `pre_close_price` = VALUES(`pre_close_price`),
                `change_rate` = VALUES(`change_rate`),
                `fetched_at` = VALUES(`fetched_at`),
                `market_phase` = VALUES(`market_phase`)
        """,
                   row.get("code"),
                   row.get("name"),
                   row.get("date"),
                   current_price,
                   pre_close_price,
                   change_rate,
                   now.strftime("%Y-%m-%d %H:%M:%S"),
                   phase)


def _sync_indicator_cache_for_prices(db, stock_codes, now):
    """用现价同步更新 MA120/反弹/回落缓存中的 close_price 和百分比值。"""
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


def _ma120_trade_signal(change_rate, pre_close_price, ma120_position, ma120):
    """判断 MA120 位置的买卖点信号。

    以 MA120 相对位置每 10% 为一个阶段，现价跨越阶段边界时触发提示：
    买点：涨跌幅为负、最新位置位于 0% 以下，且从更高阶段跨入更低阶段。
    卖点：涨跌幅为正、最新位置位于 0% 以上，且从更低阶段跨入更高阶段。
    涨跌幅来自行情接口，收盘后仍有效（收盘后昨收与现价重合，无法再用现价比较判断涨跌）。
    昨日阶段位置用昨日收盘价（K线缓存最新一根，前复权同口径）计算。
    返回 "buy"、"sell" 或空字符串。
    """
    if change_rate is None or pre_close_price is None or pre_close_price <= 0:
        return ""
    if ma120_position is None or ma120 is None or ma120 <= 0:
        return ""
    prev_position = (pre_close_price / ma120 - 1) * 100
    stage_diff = _ma120_stage(prev_position) - _ma120_stage(ma120_position)
    if change_rate < 0 and ma120_position < 0 and stage_diff > 0:
        return "buy"
    if change_rate > 0 and ma120_position > 0 and stage_diff < 0:
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


def _kline_refresh_window(now):
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


def _expected_kline_date(now):
    """K线缓存应包含的最晚已完成交易日（YYYY-MM-DD 字符串）。

    工作日收盘后：当天K线已完成，期望今天；
    盘中/盘前/周末：期望最近一个交易日（跨周末回到周五）。
    """
    phase = _market_phase(now)
    if phase == "after_close":
        return now.date().isoformat()
    return _previous_trading_day(now.date()).isoformat()


def _read_kline_cache(db, code):
    """读取该股票最近125根日K，返回与接口一致的升序 [(trade_date, close, high, low), ...]。"""
    rows = db.query(f"""
        SELECT `trade_date`, `close_price`, `high_price`, `low_price`
        FROM `{_KLINE_CACHE_TABLE}`
        WHERE `code` = %s
        ORDER BY `trade_date` DESC
        LIMIT 125
    """, code)
    return [(str(row["trade_date"])[:10],
             _to_float(row.get("close_price")),
             _to_float(row.get("high_price")),
             _to_float(row.get("low_price")))
            for row in reversed(rows)]


def _write_kline_cache(db, code, rows):
    """覆盖写入该股票K线缓存，最多125根，多余的直接删除。"""
    db.execute(f"DELETE FROM `{_KLINE_CACHE_TABLE}` WHERE `code` = %s", code)
    if not rows:
        return
    placeholders = ",".join(["(%s, %s, %s, %s, %s)"] * len(rows))
    values = [v for row in rows for v in (code, row[0], row[1], row[2], row[3])]
    db.execute(f"""
        INSERT INTO `{_KLINE_CACHE_TABLE}`
            (`code`, `trade_date`, `close_price`, `high_price`, `low_price`)
        VALUES {placeholders}
    """, *values)


def _read_recent_kline_closes(db, stock_codes):
    """读取每股K线缓存最近两根的收盘价（前复权），返回 {code: [(日期, 收盘), (日期, 收盘)]}（新→旧）。

    盘中最新一根为上一交易日收盘；
    收盘后当天K线已入库时，最新一根为当天、倒数第二根代表昨日，供买卖点信号盘后使用。
    """
    if not stock_codes:
        return {}
    placeholders = ",".join(["%s"] * len(stock_codes))
    rows = db.query(f"""
        SELECT code, rn, trade_date, close_price FROM (
            SELECT `code`, `trade_date`, `close_price`,
                   ROW_NUMBER() OVER (PARTITION BY `code` ORDER BY `trade_date` DESC) AS rn
            FROM `{_KLINE_CACHE_TABLE}`
            WHERE `code` IN ({placeholders})
        ) t WHERE rn <= 2 ORDER BY `code`, rn
    """, *stock_codes)
    result = {}
    for row in rows:
        result.setdefault(row["code"], []).append(
            (str(row["trade_date"])[:10], _to_float(row.get("close_price"))))
    return result


def _has_pending_ex_dividend(db, code, cached_max_date):
    """缓存最新一根是除息日时，前复权价可能抓取于除息调整生效前，需要重新请求。

    前复权（qfq）价格在除息日会整体调整，日期判断无法发现，
    借助派息缓存的除息日（EX_DIVIDEND_DATE）强制更新：
    除息日 >= 缓存最新交易日 即重新请求125根。
    """
    try:
        history, _, _ = dividend._get_cached_dividend_history(db, code)
    except Exception:
        return False
    for item in history or []:
        ex_date = _date_text(item.get("EX_DIVIDEND_DATE"))
        if ex_date and ex_date >= cached_max_date:
            return True
    return False


def _refresh_kline_metrics(stock_codes):
    """刷新 MA120/反弹/回落：K线缓存已含最新交易日则直接用缓存计算，否则重新请求覆盖。

    K线缓存为空或缺少最新交易日K线（如容器停机未更新）时请求一次125根并覆盖；
    缓存已含最新交易日时不再请求外部接口，仅用缓存重算并写入过期的指标缓存。
    """
    global _KLINE_REFRESH_RUNNING
    db = None
    try:
        db = mysql.Connection(**mdb.MYSQL_CONN)
        _ensure_cache_tables(db)
        now = _now()
        phase = _market_phase(now)
        # 下午3点前：日期朝前一交易日挪，排除当日未完成K线
        effective_today = now.date() if phase in ("intraday", "before_open") else None
        expected_date = _expected_kline_date(now)
        # 读取实时价格，优先用于位置计算
        price_rows = _read_price_cache(db, stock_codes)
        price_by_code = {row["code"]: row for row in price_rows}
        for code in stock_codes:
            ma120_row = _read_ma120_cache(db, [code]).get(code)
            low20_row = _read_low20_cache(db, [code]).get(code)
            high20_row = _read_high20_cache(db, [code]).get(code)
            if not (_is_ma120_cache_stale(ma120_row, now)
                    or _is_low20_cache_stale(low20_row, now)
                    or _is_high20_cache_stale(high20_row, now)):
                continue

            price_row = price_by_code.get(code)
            current_price = _to_float(price_row.get("current_price")) if price_row else None

            rows = _read_kline_cache(db, code)
            if not rows or rows[-1][0] < expected_date or _has_pending_ex_dividend(db, code, rows[-1][0]):
                rows = stocklist.fetch_daily_kline_rows(code, today=effective_today)
                if rows is None or not rows:
                    continue
                _write_kline_cache(db, code, rows)

            metrics = stocklist.compute_kline_metrics(rows, current_price)
            if metrics.get("ma120") is not None and _is_ma120_cache_stale(ma120_row, now):
                _write_ma120_cache(db, code, metrics["ma120"], now)
            if metrics.get("low20") is not None and _is_low20_cache_stale(low20_row, now):
                _write_low20_cache(db, code, metrics["low20"], now)
            if metrics.get("high20") is not None and _is_high20_cache_stale(high20_row, now):
                _write_high20_cache(db, code, metrics["high20"], now)
    except Exception as error:
        print(f"market_quotes._refresh_kline_metrics处理异常：{error}")
    finally:
        if db is not None:
            db.close()
        with _KLINE_REFRESH_LOCK:
            _KLINE_REFRESH_RUNNING = False


def _schedule_kline_refresh(stock_codes):
    global _KLINE_REFRESH_RUNNING
    stock_codes = tuple(dict.fromkeys(stock_codes))
    if not stock_codes:
        return
    window = _kline_refresh_window(_now())
    if window is None:
        return
    with _KLINE_REFRESH_LOCK:
        if _KLINE_REFRESH_RUNNING:
            return
        stock_codes = tuple(
            code for code in stock_codes
            if _KLINE_REFRESH_ATTEMPTS.get(code) != window
        )
        if not stock_codes:
            return
        for code in stock_codes:
            _KLINE_REFRESH_ATTEMPTS[code] = window
        _KLINE_REFRESH_RUNNING = True

    thread = threading.Thread(target=_refresh_kline_metrics, args=(stock_codes,), daemon=True)
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


