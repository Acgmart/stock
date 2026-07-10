#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path
import time
import random
import threading

__author__ = 'myh '
__date__ = '2025/12/31 '

class eastmoney_fetcher:
    """
    东方财富网数据获取器
    封装了Cookie管理、会话管理和请求发送功能
    """

    _request_interval = 2
    _last_request_time = 0
    _request_lock = threading.Lock()

    def __init__(self):
        """初始化获取器"""
        self.base_dir = os.path.dirname(os.path.dirname(__file__))
        self.session = self._create_session()

    def _get_cookie(self):
        """
        获取东方财富网的Cookie
        优先级：环境变量 > 文件 > 默认Cookie
        """
        # 1. 尝试从环境变量获取
        cookie = os.environ.get('EAST_MONEY_COOKIE')
        if cookie:
            # print("环境变量中的Cookie: 已设置")
            return cookie

        # 2. 尝试从文件获取
        cookie_file = Path(os.path.join(self.base_dir, 'config', 'eastmoney_cookie.txt'))
        if cookie_file.exists():
            with open(cookie_file, 'r') as f:
                cookie = f.read().strip()
            if cookie:
                # print("文件中的Cookie: 已设置")
                return cookie

        # 3. 默认Cookie（可能过期，仅作为备选）
        return 'st_si=78948464251292; st_psi=20260205091253851-119144370567-1089607836; st_pvi=07789985376191; st_sp=2026-02-05%2009%3A11%3A13; st_inirUrl=https%3A%2F%2Fxuangu.eastmoney.com%2FResult; st_sn=12; st_asi=20260205091253851-119144370567-1089607836-webznxg.dbssk.qxg-1'

    def _create_session(self):
        """创建并配置会话"""
        session = requests.Session()

        # 配置连接池
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=50,  # 增加连接池大小
            pool_maxsize=50  # 增加连接池最大大小
        )

        # 为http和https请求添加适配器
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 设置请求头
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://quote.eastmoney.com/',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        session.headers.update(headers)
        # 设置Cookie
        cookie = self._get_cookie()
        if cookie:
            session.headers.update({'Cookie': cookie})
        return session

    def _normalize_eastmoney_url(self, url):
        """Use the stable Eastmoney host and HTTPS where supported."""
        push2_hosts = (
            "80.push2.eastmoney.com",
            "82.push2.eastmoney.com",
            "88.push2.eastmoney.com",
        )
        for host in push2_hosts:
            url = url.replace(f"://{host}", "://push2.eastmoney.com")

        if url.startswith("http://") and "eastmoney.com" in url:
            url = "https://" + url[len("http://"):]
        return url

    def _throttle_request(self):
        with self._request_lock:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._request_interval:
                time.sleep(self._request_interval - elapsed)
            self.__class__._last_request_time = time.time()

    def make_request(self, url, params=None, retry=3, timeout=10):
        """
        发送请求
        :param url: 请求URL
        :param params: 请求参数
        :param retry: 重试次数
        :param timeout: 超时时间
        :return: 响应对象
        """

        url = self._normalize_eastmoney_url(url)

        for i in range(retry):
            try:
                self._throttle_request()
                response = self.session.get(
                    url,
                    params=params,
                    timeout=timeout
                )
                response.raise_for_status()  # 检查HTTP错误
                return response
            except requests.exceptions.RequestException as e:
                print(f"请求错误: {e}, 第 {i + 1}/{retry} 次重试")
                if i < retry - 1:
                    # 随机延迟后重试
                    time.sleep(random.uniform(1, 3))
                else:
                    raise
