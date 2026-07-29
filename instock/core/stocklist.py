#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re

import requests

__author__ = 'myh '
__date__ = '2026/5/12 '

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
    """Fetch market_cap (f20) and industry (f100) from Eastmoney batch API once daily."""
    if not codes:
        return {}
    secids = ",".join(f"{'1' if c.startswith('6') else '0'}.{c}" for c in codes)
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "secids": secids,
        "fields": "f12,f20,f100",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        result = {}
        if payload.get("data") and payload["data"].get("diff"):
            for item in payload["data"]["diff"]:
                code = item.get("f12", "")
                market_cap = _to_float(item.get("f20"))
                result[code] = {
                    "market_cap": market_cap / 100000000 if market_cap else None,  # 转为亿
                    "industry_name": item.get("f100", ""),
                }
        return result
    except Exception:
        return {}


def make_selected_stock_rows(date):
    codes = [code for code in get_stock_codes() if is_a_stock_code(code)]
    if not codes:
        return None

    symbols = ",".join(f"{'sh' if code.startswith('6') else 'sz'}{code}" for code in codes)
    url = f"https://hq.sinajs.cn/list={symbols}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn/",
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    response.encoding = "gb18030"

    rows = []
    for item in response.text.strip().split(";"):
        if not item.strip() or '="' not in item:
            continue
        symbol = item.split("=", 1)[0].rsplit("_", 1)[-1]
        code = symbol[-6:]
        fields = item.split('="', 1)[1].strip('"').split(",")
        if len(fields) < 32 or not fields[0]:
            continue

        open_price = _to_float(fields[1])
        pre_close = _to_float(fields[2])
        new_price = _to_float(fields[3])
        high_price = _to_float(fields[4])
        low_price = _to_float(fields[5])
        volume = _to_float(fields[8])
        deal_amount = _to_float(fields[9])
        change = None if new_price is None or pre_close in (None, 0) else new_price - pre_close
        change_rate = None if change is None else change / pre_close * 100
        amplitude = None if high_price is None or low_price is None or pre_close in (None, 0) else (
            high_price - low_price) / pre_close * 100

        rows.append({
            "date": date.strftime("%Y-%m-%d") if date is not None else fields[30],
            "code": code,
            "name": fields[0],
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


def _to_float(value):
    try:
        return float(value)
    except Exception:
        return None
