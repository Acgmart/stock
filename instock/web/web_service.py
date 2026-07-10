#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import logging
import os.path
import sys
import datetime
from zoneinfo import ZoneInfo

import tornado.httpclient
import tornado.httpserver
import tornado.ioloop
import tornado.options

# 在项目运行时，临时将项目路径添加到环境变量
cpath_current = os.path.dirname(os.path.dirname(__file__))
cpath = os.path.abspath(os.path.join(cpath_current, os.pardir))
sys.path.append(cpath)
log_path = os.path.join(cpath_current, 'log')
if not os.path.exists(log_path):
    os.makedirs(log_path)
logging.basicConfig(format='%(asctime)s %(message)s', filename=os.path.join(log_path, 'stock_web.log'))
logging.getLogger().setLevel(logging.ERROR)
import instock.lib.database as mdb
import instock.lib.mysql as mysql
import instock.web.highDividendHandler as highDividendHandler
import instock.web.base as webBase

__author__ = 'myh '
__date__ = '2026/5/12 '

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_HIGH_DIVIDEND_SNAPSHOT_HOUR = 16
_HIGH_DIVIDEND_SNAPSHOT_RETRY_SECONDS = 10 * 60


def _is_snapshot_day(value):
    return value.weekday() < 5


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            # 设置路由
            (r"/", HomeHandler),
            (r"/instock/", HomeHandler),
            (r"/instock/high_dividend", highDividendHandler.HighDividendPageHandler),
            (r"/instock/high_dividend/api", highDividendHandler.HighDividendDataHandler),
            (r"/instock/high_dividend/position", highDividendHandler.HighDividendPositionHandler),
            (r"/instock/high_dividend/fcf", highDividendHandler.HighDividendFcfHandler),
        ]
        settings = dict(  # 配置
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            xsrf_cookies=False,  # True,
            # cookie加密
            cookie_secret="027bb1b670eddf0392cdda8709268a17b58b7",
            debug=True,
        )
        super(Application, self).__init__(handlers, **settings)
        # Have one global connection to the blog DB across all handlers
        self.db = mysql.Connection(**mdb.MYSQL_CONN)


# 首页handler。
class HomeHandler(webBase.BaseHandler):
    def get(self):
        self.render("high_dividend.html")


def _schedule_high_dividend_snapshot(port):
    io_loop = tornado.ioloop.IOLoop.current()
    snapshot_url = f"http://127.0.0.1:{port}/instock/high_dividend/api"

    def next_snapshot_at(now):
        target = now.replace(
            hour=_HIGH_DIVIDEND_SNAPSHOT_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now >= target:
            target += datetime.timedelta(days=1)
        while not _is_snapshot_day(target):
            target += datetime.timedelta(days=1)
        return target

    def schedule_next():
        now = datetime.datetime.now(_SHANGHAI_TZ)
        target = next_snapshot_at(now)
        delay_seconds = max(1, (target - now).total_seconds())
        io_loop.call_later(delay_seconds, lambda: io_loop.spawn_callback(run_snapshot))
        logging.error(f"高股息列表快照下次生成时间：{target.strftime('%Y-%m-%d %H:%M:%S')}")

    async def run_snapshot():
        now = datetime.datetime.now(_SHANGHAI_TZ)
        try:
            client = tornado.httpclient.AsyncHTTPClient()
            response = await client.fetch(snapshot_url, request_timeout=300)
            logging.error(f"高股息列表快照生成完成：HTTP {response.code}")
            schedule_next()
        except Exception as error:
            logging.error(f"高股息列表快照生成失败：{error}")
            retry_at = now + datetime.timedelta(seconds=_HIGH_DIVIDEND_SNAPSHOT_RETRY_SECONDS)
            if retry_at.date() == now.date():
                io_loop.call_later(
                    _HIGH_DIVIDEND_SNAPSHOT_RETRY_SECONDS,
                    lambda: io_loop.spawn_callback(run_snapshot),
                )
            else:
                schedule_next()

    now = datetime.datetime.now(_SHANGHAI_TZ)
    today_target = now.replace(
        hour=_HIGH_DIVIDEND_SNAPSHOT_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    if now >= today_target and _is_snapshot_day(now):
        io_loop.call_later(5, lambda: io_loop.spawn_callback(run_snapshot))
    else:
        schedule_next()


def main():
    # tornado.options.parse_command_line()
    tornado.options.options.logging = None

    http_server = tornado.httpserver.HTTPServer(Application())
    port = 9988
    http_server.listen(port)
    _schedule_high_dividend_snapshot(port)

    print(f"服务已启动，web地址 : http://localhost:{port}/")
    logging.error(f"服务已启动，web地址 : http://localhost:{port}/")

    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
