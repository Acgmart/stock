#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import json
import threading

import instock.core.stocklist as stocklist
import instock.core.followlist as followlist
import instock.core.blocklist as blocklist
import instock.web.base as webBase
from instock.core.common import (
    _to_float,
    _now,
    _date_text,
    _json_default,
    _market_phase,
    _previous_trading_day,
    _ensure_cache_tables,
    _get_or_sync_fiscal_year_base,
)
from instock.core.market_quotes import (
    _get_cached_price_rows,
    _read_ma120_cache,
    _read_low20_cache,
    _read_high20_cache,
    _read_recent_kline_closes,
    _is_ma120_cache_stale,
    _is_low20_cache_stale,
    _is_high20_cache_stale,
    _ma120_trade_signal,
    _schedule_kline_refresh,
)
from instock.core.profile import (
    _get_cached_profile_rows,
    _is_industry_cache_stale,
    _is_market_cap_cache_stale,
    _schedule_industry_refresh,
    _schedule_market_cap_refresh,
)
from instock.core.dividend import (
    _get_cached_dividend_history,
    _sum_fiscal_year_dividend,
    _consecutive_non_decline_years,
    _dividend_amounts_by_year,
    _schedule_dividend_history_refresh,
)
from instock.core.financial import (
    _get_cached_latest_finance_report,
    _get_cached_annual_narrow_fcf,
    _schedule_finance_report_refresh,
    _schedule_cashflow_refresh,
)

__author__ = 'myh '
__date__ = '2026/5/12 '


class HighDividendPageHandler(webBase.BaseHandler):
    def get(self):
        _ensure_cache_tables(self.db)
        self.render("high_dividend.html")


class HighDividendDataHandler(webBase.BaseHandler):
    def get(self):
        _ensure_cache_tables(self.db)
        self.set_header("Content-Type", "application/json;charset=UTF-8")
        now = _now()
        # 财年基准年份（settings 表）：跨年自动重置，旧/新财年收益随其平移
        fiscal_year_base = _get_or_sync_fiscal_year_base(self.db)
        stock_codes = [code for code in stocklist.get_stock_codes() if stocklist.is_a_stock_code(code)]
        total_stock_count = len(stock_codes)
        rows = []
        errors = []
        stock_names = stocklist.get_stock_names()

        # 屏蔽 blocklist_industry.txt 中指定的申万二级行业，被屏蔽的股票不再读取缓存、不再刷新
        blocked_industries = stocklist.get_blocked_industries()
        blocked_industry_stock_codes = set()
        if blocked_industries:
            # 先从 blocklist_industryStocks.txt 缓存读取已屏蔽股票，避免重复判断行业
            blocked_industry_stock_codes = set(blocklist.get_blocked_codes(blocklist.INDUSTRY_STOCKS_FILE))
            if blocked_industry_stock_codes:
                stock_codes = [code for code in stock_codes if code not in blocked_industry_stock_codes]
        price_by_code = _get_cached_price_rows(self.db, stock_codes, errors)
        profile_by_code = _get_cached_profile_rows(self.db, stock_codes)
        # 昨日收盘价：K线缓存最近两根收盘（前复权）——盘中最新一根即上一交易日，
        # 收盘后当天K线已入库时最新一根为当天、倒数第二根代表昨日（买卖点提示盘后依然有效）；
        # 盘前（0点至9点半开盘）与休市（周末）K线仍为上一交易日，同样用倒数第二根延续盘后提示
        kline_recent_by_code = _read_recent_kline_closes(self.db, stock_codes)
        today_text = now.date().isoformat()
        phase = _market_phase(now)
        pre_open_expected_kline_date = _previous_trading_day(now.date()).isoformat()
        if blocked_industries:
            # 未缓存的股票：命中屏蔽行业的自动记录到缓存文件并屏蔽
            for code in stock_codes:
                if code in blocked_industry_stock_codes:
                    continue
                profile = profile_by_code.get(code)
                if profile is None:
                    continue  # 暂无行业信息，本次不判断
                if (profile.get("industry_name") or "") in blocked_industries:
                    blocklist.add_blocked(blocklist.INDUSTRY_STOCKS_FILE, code, stock_names.get(code, ""))
                    blocked_industry_stock_codes.add(code)
            if blocked_industry_stock_codes:
                stock_codes = [code for code in stock_codes if code not in blocked_industry_stock_codes]
        # 屏蔽 blocklist_negativeEps.txt 中记录的收益（最新已完结财年年报稀释每股收益）为负或0的股票，被屏蔽的股票不再读取缓存、不再刷新
        blocked_negative_eps_codes = set(blocklist.get_blocked_codes(blocklist.NEGATIVE_EPS_FILE))
        if blocked_negative_eps_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_negative_eps_codes]
        # 屏蔽 blocklist_dividendYieldBelowOne.txt 中记录的股息率低于1%的股票，被屏蔽的股票不再读取缓存、不再刷新
        blocked_yield_below_one_codes = set(blocklist.get_blocked_codes(blocklist.YIELD_BELOW_ONE_FILE))
        if blocked_yield_below_one_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_yield_below_one_codes]
        # 屏蔽 blocklist_dividendGrowthYearZero.txt 中记录的息增年为0的股票，被屏蔽的股票不再读取缓存、不再刷新
        blocked_zero_growth_codes = set(blocklist.get_blocked_codes(blocklist.GROWTH_YEAR_ZERO_FILE))
        if blocked_zero_growth_codes:
            stock_codes = [code for code in stock_codes if code not in blocked_zero_growth_codes]
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
        # 行业只抓一次不刷新；市值每周刷新
        stale_industry_codes = [
            code for code in stock_codes
            if _is_industry_cache_stale(profile_by_code.get(code), now)
        ]
        stale_market_cap_codes = [
            code for code in stock_codes
            if _is_market_cap_cache_stale(profile_by_code.get(code), now)
        ]
        stale_dividend_codes = []
        stale_finance_codes = []
        stale_cashflow_codes = []
        blocked_this_run_codes = set()

        for code in stock_codes:
            price_row = price_by_code.get(code)
            current_price = None if price_row is None else _to_float(price_row.get("current_price"))
            change_rate = None if price_row is None else _to_float(price_row.get("change_rate"))
            recent = kline_recent_by_code.get(code)
            pre_close_price = None
            if recent:
                latest_date, latest_close = recent[0]
                if len(recent) > 1 and (
                    (phase == "after_close" and latest_date == today_text)
                    or (phase in ("before_open", "closed") and latest_date == pre_open_expected_kline_date)
                ):
                    # 盘后当天K线已入库 / 盘前、休市延续盘后提示：昨日收盘用倒数第二根
                    pre_close_price = recent[1][1]
                else:
                    # 盘中/盘后未更新/假日：最新一根即上一交易日收盘
                    pre_close_price = latest_close
            try:
                finance_report, finance_report_stale = _get_cached_latest_finance_report(self.db, code, fiscal_year_base)
                if finance_report_stale:
                    stale_finance_codes.append(code)
            except Exception as error:
                finance_report = {}
                errors.append(f"{code} 财报数据读取失败：{error}")

            # 最新已完结财年：检测到新财年（基准-1）年报 → 新财年，否则为旧财年（基准-2）；
            # 年报数据未抓取（finance_fetched=False）时无法判断年份，暂不计算股息率/息增年
            if finance_report.get("finance_fetched"):
                latest_fiscal_year = fiscal_year_base - 1 if finance_report.get("new_year_eps_report_date") else fiscal_year_base - 2
            else:
                latest_fiscal_year = None

            try:
                history, dividend_changed, dividend_history_stale = _get_cached_dividend_history(self.db, code)
                if dividend_history_stale:
                    stale_dividend_codes.append(code)
                dividend_year = latest_fiscal_year
                if dividend_year is None:
                    # 年报数据未抓取：无法判断财年，股息率/息增年暂不计算（显示--），等抓取完成后计算
                    dividend_per_10 = None
                    dividend_per_share = None
                    dividend_yield = None
                    dividend_growth_years = None
                    dividend_amount_by_year = []
                    details = []
                else:
                    dividend_per_10, details = _sum_fiscal_year_dividend(history, dividend_year)
                    dividend_growth_years = _consecutive_non_decline_years(history, dividend_year)
                    # 息增年悬浮提示：连续增长段（growth+1 年）加中断对比年，共 growth+2 年，每股派息额
                    year_totals = _dividend_amounts_by_year(history)
                    first_year = min(year_totals) if year_totals else dividend_year
                    window_start = max(first_year, dividend_year - dividend_growth_years - 1)
                    dividend_amount_by_year = [
                        {"year": year, "per_share": round(year_totals.get(year, 0.0) / 10, 4)}
                        for year in range(dividend_year, window_start - 1, -1)
                    ]
                    dividend_per_share = dividend_per_10 / 10
                    # 股息率未知（派息历史未抓取）时为 None 显示 --；
                    # 派息历史已抓取但最近财年无派息时股息率真实为 0（由股息率<1%规则屏蔽）
                    dividend_yield = None
                    if dividend_per_share > 0 and current_price and current_price > 0:
                        dividend_yield = dividend_per_share / current_price * 100
                    elif dividend_per_share <= 0 and (history or not dividend_history_stale):
                        dividend_yield = 0.0
            except Exception as error:
                # history/dividend_history_stale 供后续屏蔽判断使用，读取失败按未抓取处理
                history = []
                dividend_history_stale = True
                dividend_year = latest_fiscal_year
                dividend_per_10 = 0.0
                dividend_per_share = 0.0
                dividend_yield = None
                details = []
                dividend_changed = False
                dividend_growth_years = 0
                dividend_amount_by_year = []
                errors.append(f"{code} 派息数据读取失败：{error}")

            # 屏蔽优先级：行业（请求开始已处理）→ 收益 → 股息率 → 息增年
            # 收益（最新已完结财年年报稀释每股收益）为负或0：自动记录到 blocklist_negativeEps.txt 并屏蔽，不再读取缓存、不再刷新
            if finance_report.get("diluted_eps") is not None and finance_report.get("diluted_eps") <= 0:
                blocklist.add_blocked(blocklist.NEGATIVE_EPS_FILE, code, stock_names.get(code, ""))
                blocked_this_run_codes.add(code)
                continue
            # 股息率低于1%（只需派息历史与价格）：自动记录到 blocklist_dividendYieldBelowOne.txt 并屏蔽，不再读取缓存、不再刷新
            if dividend_yield is not None and dividend_yield < 1 and history:
                blocklist.add_blocked(blocklist.YIELD_BELOW_ONE_FILE, code, stock_names.get(code, ""))
                blocked_this_run_codes.add(code)
                continue
            # 息增年为0（只需派息历史）：自动记录到 blocklist_dividendGrowthYearZero.txt 并屏蔽，不再读取缓存、不再刷新
            # 新上市无分红公司派息历史凑不出2个财年，息增年同样为0：
            # 派息历史非空，或已确认检查过（缓存不旧）即为空，都视为息增年为0屏蔽；
            # 尚未抓取到派息历史（缓存过期待抓）的不屏蔽，避免误杀。
            if dividend_growth_years == 0 and (history or not dividend_history_stale):
                blocklist.add_blocked(blocklist.GROWTH_YEAR_ZERO_FILE, code, stock_names.get(code, ""))
                blocked_this_run_codes.add(code)
                continue

            try:
                annual_fcf, cashflow_stale = _get_cached_annual_narrow_fcf(self.db, code)
                if cashflow_stale:
                    stale_cashflow_codes.append(code)
            except Exception as error:
                annual_fcf = {}
                errors.append(f"{code} 现金流数据读取失败：{error}")

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
            elif annual_fcf.get("narrow_fcf_skipped"):
                # 金融行业不适用窄口径FCF：用稀释每股收益替代
                # （收益/每股派息 = 派息覆盖率，收益/现价 = 盈利收益率）
                eps = finance_report.get("diluted_eps")
                if eps is not None and eps > 0:
                    if dividend_per_share > 0:
                        fcf_dividend = eps / dividend_per_share
                    if current_price and current_price > 0:
                        fcf_price = eps / current_price * 100
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
                "old_year_eps": finance_report.get("old_year_eps"),
                "old_year_eps_field": finance_report.get("old_year_eps_field", ""),
                "old_year_eps_report_date": finance_report.get("old_year_eps_report_date", ""),
                "old_year_eps_report_name": finance_report.get("old_year_eps_report_name", ""),
                "new_year_eps": finance_report.get("new_year_eps"),
                "new_year_eps_field": finance_report.get("new_year_eps_field", ""),
                "new_year_eps_report_date": finance_report.get("new_year_eps_report_date", ""),
                "new_year_eps_report_name": finance_report.get("new_year_eps_report_name", ""),
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
                "ma120_time": "" if not ma120_row else (
                    ma120_row["fetched_at"].strftime("%H:%M:%S")
                    if hasattr(ma120_row.get("fetched_at"), "strftime") else str(ma120_row.get("fetched_at") or "")[11:19]),
                "ma120_close_price": None if not ma120_row else _to_float(ma120_row.get("close_price")),
                "ma120": None if not ma120_row else _to_float(ma120_row.get("ma120")),
                "ma120_position": ma120_position,
                "ma120_signal": _ma120_trade_signal(
                    change_rate,
                    pre_close_price,
                    ma120_position,
                    None if not ma120_row else _to_float(ma120_row.get("ma120"))),
                "low20_trade_date": "" if not low20_row else _date_text(low20_row.get("trade_date")),
                "low20_time": "" if not low20_row else (
                    low20_row["fetched_at"].strftime("%H:%M:%S")
                    if hasattr(low20_row.get("fetched_at"), "strftime") else str(low20_row.get("fetched_at") or "")[11:19]),
                "low20_close_price": None if not low20_row else _to_float(low20_row.get("close_price")),
                "low20_lowest_date": "" if not low20_row else _date_text(low20_row.get("lowest_date")),
                "low20_lowest_low": None if not low20_row else _to_float(low20_row.get("lowest_low")),
                "low20_bounce": low20_bounce,
                "high20_trade_date": "" if not high20_row else _date_text(high20_row.get("trade_date")),
                "high20_time": "" if not high20_row else (
                    high20_row["fetched_at"].strftime("%H:%M:%S")
                    if hasattr(high20_row.get("fetched_at"), "strftime") else str(high20_row.get("fetched_at") or "")[11:19]),
                "high20_close_price": None if not high20_row else _to_float(high20_row.get("close_price")),
                "high20_highest_date": "" if not high20_row else _date_text(high20_row.get("highest_date")),
                "high20_highest_high": None if not high20_row else _to_float(high20_row.get("highest_high")),
                "high20_decline": high20_decline,
                "price_date": "" if price_row is None else _date_text(price_row.get("price_date")),
                "price_time": "" if price_row is None else (
                    price_row["fetched_at"].strftime("%m-%d %H:%M:%S")
                    if hasattr(price_row.get("fetched_at"), "strftime") else str(price_row.get("fetched_at") or "")[5:19]),
                "current_price": current_price,
                "change_rate": change_rate,
                "market_cap": None if profile_by_code.get(code) is None else _to_float(profile_by_code[code].get("market_cap")),
                "dividend_year": dividend_year,
                "dividend_per_10": None if dividend_per_10 is None else round(dividend_per_10, 4),
                "dividend_per_share": None if dividend_per_share is None else round(dividend_per_share, 4),
                "dividend_yield": dividend_yield,
                "dividend_growth_years": dividend_growth_years,
                "dividend_amount_by_year": dividend_amount_by_year,
                "dividend_changed": dividend_changed,
                "details": details,
            })

        # 本次新屏蔽的股票不再安排任何缓存刷新
        if blocked_this_run_codes:
            for stale_list in (
                stale_ma120_codes, stale_low20_codes, stale_high20_codes,
                stale_industry_codes, stale_market_cap_codes,
                stale_finance_codes, stale_dividend_codes,
            ):
                stale_list[:] = [code for code in stale_list if code not in blocked_this_run_codes]

        # 优先请求屏蔽相关数据（行业→收益→股息率/息增年），尽快完成屏蔽
        # 其余数据（市值、现金流、MA120、20日高低点）等屏蔽相关数据完成后才请求，屏蔽的股票不再请求
        priority_threads = [t for t in (
            _schedule_industry_refresh(stale_industry_codes),
            _schedule_finance_report_refresh(stale_finance_codes),
            _schedule_dividend_history_refresh(stale_dividend_codes),
        ) if t]

        def _schedule_tail_refreshes():
            _schedule_market_cap_refresh(stale_market_cap_codes)
            _schedule_cashflow_refresh(stale_cashflow_codes)
            # MA120/反弹/回落合并在一次K线请求中刷新
            stale_kline_codes = list(dict.fromkeys(
                stale_ma120_codes + stale_low20_codes + stale_high20_codes))
            _schedule_kline_refresh(stale_kline_codes)

        if priority_threads:
            def _run_priority_then_tail():
                for thread in priority_threads:
                    thread.join()
                _schedule_tail_refreshes()
            threading.Thread(target=_run_priority_then_tail, daemon=True).start()
        else:
            _schedule_tail_refreshes()
        rows.sort(key=lambda item: (item["dividend_yield"] is not None, item["dividend_yield"] or 0), reverse=True)
        payload = {
            "total_stock_count": total_stock_count,
            "stock_count": len(rows),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "fiscal_year_base": fiscal_year_base,
            "fiscal_year_note": "财年基准年份存于数据库 settings 表，跨年自动重置；旧财年=基准-2，新财年=基准-1，检测到新财年年报后新财年收益不再为空",
            "cache_policy": {
                "price": "盘中每5分钟刷新一次（与前端自动刷新同步），盘后保持收盘价",
                "profile": "页面请求只读缓存；行业只抓一次不刷新，市值取每周最后一个交易日收盘数据、周五收盘后刷新，无缓存立即抓取",
                "ma120": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据",
                "low20": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据",
                "high20": "页面请求只读缓存；盘中可刷新（使用前一交易日收盘数据），下午3点后刷新当日收盘数据",
                "dividend_history": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次",
                "finance_report": "页面请求只读缓存；交易日每天8点后检查一次，16点至23点最多每4小时复查一次",
                "cashflow": "页面请求只读缓存；窄口径FCF只取最新年报，金融行业不抓取；年报季交易日检查，非年报季最多7天一次",
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
