#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import threading

import requests

__author__ = 'myh '
__date__ = '2026/5/12 '

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_REQUEST_INTERVAL_SECONDS = 2


def _throttle_request():
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        elapsed = time.time() - _LAST_REQUEST_AT
        if elapsed < _REQUEST_INTERVAL_SECONDS:
            time.sleep(_REQUEST_INTERVAL_SECONDS - elapsed)
        _LAST_REQUEST_AT = time.time()

DEFAULT_STOCK_CODES = ("600900",)
_CODE_PATTERN = re.compile(r"(?<!\d)(?:sh|sz|bj)?(\d{6})(?!\d)", re.IGNORECASE)


def _candidate_paths():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return (
        os.environ.get("INSTOCK_STOCKLIST_PATH"),
        os.environ.get("STOCKLIST_PATH"),
        os.path.join(base_dir, "config", "stocklist.txt"),
    )


def _read_codes_from_file(path):
    codes = []
    if not path or not os.path.isfile(path):
        return codes

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.split("#", 1)[0].split("//", 1)[0].strip()
            if not line:
                continue
            if line in ("*", "ALL", "all"):
                return ["*"]
            match = _CODE_PATTERN.search(line)
            if match:
                codes.append(match.group(1))
    return codes


def get_stock_names():
    """从 stocklist.txt 读取股票代码到名称的映射。"""
    names = {}
    for path in _candidate_paths():
        if not path or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.split("#", 1)[0].split("//", 1)[0].strip()
                if not line:
                    continue
                match = _CODE_PATTERN.search(line)
                if match:
                    code = match.group(1)
                    name_start = match.end()
                    name = line[name_start:].strip()
                    if code not in names:
                        names[code] = name
        if names:
            return names
    return names


def get_stock_codes():
    for path in _candidate_paths():
        codes = _read_codes_from_file(path)
        if codes:
            if codes == ["*"]:
                return DEFAULT_STOCK_CODES
            return tuple(dict.fromkeys(codes))
    return DEFAULT_STOCK_CODES


def is_a_stock_code(code):
    return str(code).startswith(('600', '601', '603', '605', '000', '001', '002', '003', '300', '301'))


def fetch_profile_data(codes):
    """从腾讯获取市值，从东方财富获取行业，每日批量刷新一次。

    由于 push2.eastmoney.com 不可达，拆分为两个独立数据源：
    - 市值：腾讯 qt.gtimg.cn 行情接口，field 44 为总市值（亿）
    - 行业：东方财富 datacenter RPT_F10_ORG_BASICINFO，取 BOARD_NAME_2LEVEL（申万二级）
    """
    if not codes:
        return {}
    codes = list(codes)
    result = {code: {"market_cap": None, "industry_name": ""} for code in codes}

    # 1. 市值 — 腾讯行情接口（批量）
    _fetch_market_cap_from_tencent(codes, result)

    # 2. 行业 — 东方财富 F10 接口（批量，datacenter 域可通）
    _fetch_industry_from_eastmoney(codes, result)

    return result


def _fetch_market_cap_from_tencent(codes, result):
    """从腾讯行情接口批量获取总市值（亿）。"""
    symbols = ",".join(f"{'sh' if c.startswith('6') else 'sz'}{c}" for c in codes)
    url = f"https://qt.gtimg.cn/q={symbols}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    }
    try:
        _throttle_request()
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        for line in resp.text.strip().split(";"):
            if '="' not in line:
                continue
            name_part, data = line.split('="', 1)
            symbol = name_part.split("_")[-1]
            code = symbol[-6:]
            fields = data.strip('"').split("~")
            if len(fields) > 44:
                market_cap = _to_float(fields[44])
                if code in result and market_cap is not None:
                    result[code]["market_cap"] = market_cap
    except Exception:
        pass


def _fetch_industry_from_eastmoney(codes, result):
    """从东方财富 F10 批量获取申万二级行业。"""
    code_list = '","'.join(codes)
    url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    params = {
        "reportName": "RPT_F10_ORG_BASICINFO",
        "columns": "SECURITY_CODE,BOARD_NAME_2LEVEL",
        "filter": f'(SECURITY_CODE in ("{code_list}"))',
        "pageNumber": "1",
        "pageSize": str(len(codes) + 10),
        "source": "HSF10",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        _throttle_request()
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("success") and payload.get("result", {}).get("data"):
            for row in payload["result"]["data"]:
                code = row.get("SECURITY_CODE", "")
                if code in result:
                    result[code]["industry_name"] = row.get("BOARD_NAME_2LEVEL") or ""
    except Exception:
        pass


def make_selected_stock_rows(date):
    """从腾讯行情接口批量获取实时股价数据。

    原使用新浪 hq.sinajs.cn，该域名已不可达（403）。
    """
    codes = [code for code in get_stock_codes() if is_a_stock_code(code)]
    if not codes:
        return None

    symbols = ",".join(f"{'sh' if code.startswith('6') else 'sz'}{code}" for code in codes)
    url = f"https://qt.gtimg.cn/q={symbols}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    }
    _throttle_request()
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    rows = []
    for line in response.text.strip().split(";"):
        if '="' not in line:
            continue
        symbol_part, data = line.split('="', 1)
        symbol = symbol_part.split("_")[-1]
        code = symbol[-6:]
        fields = data.strip('"').split("~")
        if len(fields) < 38 or not fields[1]:
            continue

        # Tencent qt 字段（0-indexed）:
        # 1=名称 3=现价 4=昨收 5=今开 6=成交量(手)
        # 30=日期时间(YYYYMMDDHHMMSS) 31=涨跌额 32=涨跌幅%
        # 33=最高 34=最低 35=现价/成交量/成交额(元)
        # 37=成交额(万元)
        name = fields[1]
        open_price = _to_float(fields[5])
        pre_close = _to_float(fields[4])
        new_price = _to_float(fields[3])
        high_price = _to_float(fields[33])
        low_price = _to_float(fields[34])
        volume = _to_float(fields[6])

        # 成交额：优先从 field 35 解析（格式 现价/成交量/成交额），否则用 field 37 (万元)
        deal_amount = None
        if len(fields) > 35 and fields[35]:
            parts = fields[35].split("/")
            if len(parts) >= 3:
                deal_amount = _to_float(parts[2])
        if deal_amount is None and len(fields) > 37:
            deal_amount_wan = _to_float(fields[37])
            if deal_amount_wan is not None:
                deal_amount = deal_amount_wan * 10000

        change = None if new_price is None or pre_close in (None, 0) else new_price - pre_close
        change_rate = _to_float(fields[32])
        amplitude = None if high_price is None or low_price is None or pre_close in (None, 0) else (
            high_price - low_price) / pre_close * 100

        rows.append({
            "date": date.strftime("%Y-%m-%d") if date is not None else fields[30][:10],
            "code": code,
            "name": name,
            "new_price": new_price,
            "change_rate": change_rate,
            "ups_downs": change,
            "volume": volume,
            "deal_amount": deal_amount,
            "amplitude": amplitude,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "pre_close_price": pre_close,
        })

    return rows or None


def fetch_daily_ma120_position(code, today=None):
    """获取日K线MA120位置，使用腾讯前复权价格。

    腾讯K线API返回前复权（qfq）日线数据，
    确保MA120计算时历史价格已就除权除息进行调整，
    与主流股票APP的MA120数值一致。
    """
    market = "sh" if code.startswith("6") else "sz"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"{market}{code},day,,,170,qfq",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    }
    _throttle_request()
    response = requests.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()

    payload = response.json()
    stock_data = payload.get("data", {}).get(f"{market}{code}")
    if not stock_data:
        return None
    klines = stock_data.get("qfqday") or stock_data.get("day")
    if not klines:
        return None

    rows = []
    for item in klines:
        if len(item) < 3:
            continue
        # item format: [date, open, close, high, low, volume, ...]
        close_price = _to_float(item[2])
        if close_price is None or close_price <= 0:
            continue
        trade_date = str(item[0])[:10]
        if not trade_date:
            continue
        rows.append((trade_date, close_price))

    if today is not None:
        today_text = today.strftime("%Y-%m-%d")
        rows = [row for row in rows if row[0] < today_text]

    if len(rows) < 120:
        return None

    trade_date, close_price = rows[-1]
    ma120 = sum(close for _, close in rows[-120:]) / 120
    if ma120 <= 0:
        return None

    return {
        "trade_date": trade_date,
        "close_price": close_price,
        "ma120": ma120,
        "ma120_position": (close_price / ma120 - 1) * 100,
    }


def fetch_20day_low_bounce(code):
    """获取最近收盘价相对于最近20个交易日盘中最低价的反弹幅度，使用腾讯前复权价格。"""
    market = "sh" if code.startswith("6") else "sz"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"{market}{code},day,,,25,qfq",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://gu.qq.com/",
    }
    _throttle_request()
    response = requests.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()

    payload = response.json()
    stock_data = payload.get("data", {}).get(f"{market}{code}")
    if not stock_data:
        return None
    klines = stock_data.get("qfqday") or stock_data.get("day")
    if not klines or len(klines) < 20:
        return None

    rows = []
    for item in klines:
        if len(item) < 5:
            continue
        # item format: [date, open, close, high, low, volume, ...]
        close_price = _to_float(item[2])
        low_price = _to_float(item[4])
        if close_price is None or close_price <= 0:
            continue
        if low_price is None or low_price <= 0:
            continue
        trade_date = str(item[0])[:10]
        if not trade_date:
            continue
        rows.append((trade_date, close_price, low_price))

    if len(rows) < 20:
        return None

    recent_20 = rows[-20:]
    lowest_row = min(recent_20, key=lambda r: r[2])
    lowest_trade_date, _, lowest_low = lowest_row
    current_trade_date, current_close, _ = rows[-1]

    if lowest_low <= 0:
        return None

    return {
        "trade_date": current_trade_date,
        "close_price": current_close,
        "lowest_date": lowest_trade_date,
        "lowest_low": lowest_low,
        "bounce_position": (current_close / lowest_low - 1) * 100,
    }


def _to_float(value):
    try:
        return float(value)
    except Exception:
        return None
