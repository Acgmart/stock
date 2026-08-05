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
    _ensure_cache_tables,
)
from instock.core.market_quotes import (
    _get_cached_price_rows,
    _read_ma120_cache,
    _read_low20_cache,
    _read_high20_cache,
    _is_ma120_cache_stale,
    _is_low20_cache_stale,
    _is_high20_cache_stale,
    _ma120_trade_signal,
    _schedule_ma120_refresh,
    _schedule_low20_refresh,
    _schedule_high20_refresh,
)
from instock.core.profile import (
    _get_cached_profile_rows,
    _is_profile_cache_stale,
    _schedule_profile_refresh,
)
from instock.core.dividend import (
    _get_cached_dividend_history,
    _latest_dividend_year,
    _sum_fiscal_year_dividend,
    _consecutive_non_decline_years,
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
                dividend_per_share = dividend_per_10 / 10
                dividend_yield = None
                if current_price and current_price > 0:
                    dividend_yield = dividend_per_share / current_price * 100
                # 优先屏蔽：息增年为0（只需派息历史）
                if dividend_growth_years == 0 and history:
                    # 息增年为0：自动记录到 blocklist_dividendGrowthYearZero.txt 并屏蔽，不再读取缓存、不再刷新
                    blocklist.add_blocked(blocklist.GROWTH_YEAR_ZERO_FILE, code, stock_names.get(code, ""))
                    blocked_this_run_codes.add(code)
                    continue
                # 优先屏蔽：股息率低于1%（只需派息历史与价格，无需财报/现金流）
                if dividend_yield is not None and dividend_yield < 1 and history:
                    # 股息率低于1%：自动记录到 blocklist_dividendYieldBelowOne.txt 并屏蔽，不再读取缓存、不再刷新
                    blocklist.add_blocked(blocklist.YIELD_BELOW_ONE_FILE, code, stock_names.get(code, ""))
                    blocked_this_run_codes.add(code)
                    continue
            except Exception as error:
                dividend_year = now.year - 1
                dividend_per_10 = 0.0
                dividend_per_share = 0.0
                dividend_yield = None
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

        # 优先请求屏蔽文件相关数据（派息历史→息增年/股息率、财报→收益、行业），尽快完成屏蔽
        # 其余数据（现金流、MA120、20日高低点）等屏蔽相关数据完成后才请求，屏蔽的股票不再请求
        priority_threads = [t for t in (
            _schedule_dividend_history_refresh(stale_dividend_codes),
            _schedule_finance_report_refresh(stale_finance_codes),
            _schedule_profile_refresh(stale_profile_codes),
        ) if t]

        def _schedule_tail_refreshes():
            _schedule_cashflow_refresh(stale_cashflow_codes)
            _schedule_ma120_refresh(stale_ma120_codes)
            _schedule_low20_refresh(stale_low20_codes)
            _schedule_high20_refresh(stale_high20_codes)

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
