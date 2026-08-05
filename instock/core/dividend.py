#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import json
import threading

import instock.lib.database as mdb
import instock.lib.mysql as mysql
from instock.core.common import (
    _to_float,
    _now,
    _date_text,
    _json_default,
    _throttle_external_request,
    _ensure_cache_tables,
    _history_hash,
    _changed_report_date,
    _is_daily_report_cache_stale,
    _is_in_changed_display_window,
    _DIVIDEND_FETCHER,
    _DIVIDEND_HISTORY_CACHE_TABLE,
    _REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

_DIVIDEND_REFRESH_LOCK = threading.Lock()
_DIVIDEND_REFRESH_RUNNING = False


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
        print(f"dividend._refresh_dividend_histories处理异常：{error}")
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
    return thread


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
