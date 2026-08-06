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
    _FINANCE_REPORT_CACHE_TABLE,
    _CASHFLOW_CACHE_TABLE,
    _REPORT_AFTER_CLOSE_REFRESH_INTERVAL_HOURS,
    _CASHFLOW_OFFSEASON_REFRESH_DAYS,
)

__author__ = 'myh '
__date__ = '2026/8/6 '

_FINANCE_REPORT_REFRESH_LOCK = threading.Lock()
_FINANCE_REPORT_REFRESH_RUNNING = False
_CASHFLOW_REFRESH_LOCK = threading.Lock()
_CASHFLOW_REFRESH_RUNNING = False


_FINANCE_BATCH_SIZE = 20
_FINANCE_BATCH_PAGE_SIZE = 500


def _fetch_finance_reports_batch(codes):
    """批量抓取扣非/收益（RPT_F10_FINANCE_MAINFINADATA），每批20只一次请求，返回 {code: history}。

    财报历史每股最多20条（pageSize=20），20只一批、pageSize=500 不截断。
    批次抓取失败时该批股票不出现在结果中（保持原缓存，下次重试）。
    """
    result = {}
    if not codes:
        return result
    for i in range(0, len(codes), _FINANCE_BATCH_SIZE):
        batch = codes[i:i + _FINANCE_BATCH_SIZE]
        code_list = '","'.join(batch)
        _throttle_external_request()
        url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        params = {
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": "ALL",
            "quoteColumns": "",
            "filter": f'(SECURITY_CODE in ("{code_list}"))',
            "pageNumber": "1",
            "pageSize": str(_FINANCE_BATCH_PAGE_SIZE),
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
            "source": "HSF10",
        }
        try:
            response = _DIVIDEND_FETCHER.make_request(url, params=params, timeout=15)
            payload = response.json()
            if payload.get("success") and payload.get("result"):
                for code in batch:
                    result.setdefault(code, [])
                for row in payload["result"].get("data") or []:
                    code = row.get("SECURITY_CODE")
                    if code in batch:
                        result[code].append(row)
        except Exception:
            continue
    return result


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
        now = _now()
        # 先过滤过期股票，再批量抓取（每批20只）
        need_codes = []
        for code in stock_codes:
            cache_row, history = _read_finance_report_cache(db, code)
            if _is_finance_report_cache_stale(cache_row, history, now):
                need_codes.append(code)
        if not need_codes:
            return
        histories = _fetch_finance_reports_batch(need_codes)
        for code in need_codes:
            if code not in histories:
                continue  # 所在批次抓取失败，保持原缓存，下次重试
            fresh_history = histories[code]
            cache_row, history = _read_finance_report_cache(db, code)
            old_growth = _latest_finance_report_deducted_growth(history)
            fresh_growth = _latest_finance_report_deducted_growth(fresh_history)
            changed = bool(history) and fresh_growth is not None and fresh_growth != old_growth
            _write_finance_report_cache(db, code, fresh_history, now, changed)
    except Exception as error:
        print(f"financial._refresh_finance_reports处理异常：{error}")
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
    return thread


def _latest_finance_report(history):
    if not history:
        return None
    return sorted(history, key=lambda item: _date_text(item.get("REPORT_DATE")), reverse=True)[0]


def _annual_eps_fields(row, prefix="", base="diluted_eps"):
    """提取年报记录的稀释每股收益字段；prefix+base 区分最新/旧财年/新财年。"""
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
        f"{prefix}{base}": eps,
        f"{prefix}{base}_field": eps_field,
        f"{prefix}{base}_report_date": _date_text(row.get("REPORT_DATE")),
        f"{prefix}{base}_report_name": row.get("REPORT_DATE_NAME") or row.get("REPORT_TYPE") or "",
    }


def _latest_annual_diluted_eps(history):
    return _annual_eps_fields(_latest_annual_report(history))


def _annual_report_for_year(history, year):
    """返回指定财年的年报记录；该财年年报未检测到时返回 None。"""
    annual_rows = [
        item for item in history or []
        if _is_annual_report_row(item)
        and _date_text(item.get("REPORT_DATE")).startswith(str(year))
    ]
    if not annual_rows:
        return None
    return sorted(annual_rows, key=lambda item: _date_text(item.get("REPORT_DATE")), reverse=True)[0]


def _get_cached_latest_finance_report(db, code, fiscal_year_base=None):
    now = _now()
    cache_row, history = _read_finance_report_cache(db, code)
    is_stale = _is_finance_report_cache_stale(cache_row, history, now)
    changed_at = None if cache_row is None else cache_row.get("changed_at")
    changed_report_date = None if cache_row is None else cache_row.get("changed_report_date")
    report_changed = _is_in_changed_display_window(changed_at, changed_report_date, now)
    annual_eps = _latest_annual_diluted_eps(history)
    # 旧财年（基准-2）年报必然已披露；新财年（基准-1）年报检测到才非空，
    # 两者配合即可判断当前已完结的最新财年是哪一年；基准年份存于 settings 表。
    # finance_fetched：是否已抓取到年报数据（有缓存行），未抓取时无法判断财年
    base = fiscal_year_base if fiscal_year_base is not None else now.year
    old_year_eps = _annual_eps_fields(_annual_report_for_year(history, base - 2), prefix="old_year_", base="eps")
    new_year_eps = _annual_eps_fields(_annual_report_for_year(history, base - 1), prefix="new_year_", base="eps")
    row = _latest_finance_report(history)
    if row is None:
        annual_eps["report_changed"] = False
        return {**annual_eps, **old_year_eps, **new_year_eps, "finance_fetched": cache_row is not None}, is_stale
    return {
        "deducted_profit_growth": _to_float(row.get("KCFJCXSYJLRTZ")),
        "report_date": _date_text(row.get("REPORT_DATE")),
        "report_name": row.get("REPORT_DATE_NAME") or row.get("REPORT_TYPE") or "",
        "notice_date": _date_text(row.get("NOTICE_DATE")),
        "deducted_profit": _to_float(row.get("KCFJCXSYJLR")),
        "report_changed": report_changed,
        **annual_eps,
        **old_year_eps,
        **new_year_eps,
        "finance_fetched": cache_row is not None,
    }, is_stale


_CASHFLOW_BATCH_SIZE = 20
_CASHFLOW_BATCH_PAGE_SIZE = 500


def _fetch_cashflows_batch(codes):
    """批量抓取FCF现金流（RPT_DMSK_FN_CASHFLOW），每批20只一次请求，返回 {code: history}。

    现金流历史每股最多20条（pageSize=20），20只一批、pageSize=500 不截断。
    批次抓取失败时该批股票不出现在结果中（保持原缓存，下次重试）。
    """
    result = {}
    if not codes:
        return result
    for i in range(0, len(codes), _CASHFLOW_BATCH_SIZE):
        batch = codes[i:i + _CASHFLOW_BATCH_SIZE]
        code_list = '","'.join(batch)
        _throttle_external_request()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_DMSK_FN_CASHFLOW",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE in ("{code_list}"))',
            "pageNumber": "1",
            "pageSize": str(_CASHFLOW_BATCH_PAGE_SIZE),
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        try:
            response = _DIVIDEND_FETCHER.make_request(url, params=params, timeout=15)
            payload = response.json()
            if payload.get("success") and payload.get("result"):
                for code in batch:
                    result.setdefault(code, [])
                for row in payload["result"].get("data") or []:
                    code = row.get("SECURITY_CODE")
                    if code in batch:
                        result[code].append(row)
        except Exception:
            continue
    return result


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
        now = _now()
        # 先过滤过期股票，再批量抓取（每批20只）
        need_codes = []
        for code in stock_codes:
            cache_row, history = _read_cashflow_cache(db, code)
            if _is_cashflow_cache_stale(cache_row, history, now):
                need_codes.append(code)
        if not need_codes:
            return
        histories = _fetch_cashflows_batch(need_codes)
        for code in need_codes:
            if code not in histories:
                continue  # 所在批次抓取失败，保持原缓存，下次重试
            _write_cashflow_cache(db, code, histories[code], now)
    except Exception as error:
        print(f"financial._refresh_cashflows处理异常：{error}")
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
    return thread


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
