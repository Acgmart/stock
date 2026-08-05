#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import threading

__author__ = 'myh '
__date__ = '2026/8/5 '

_BLOCK_LOCK = threading.Lock()
_CODE_PATTERN = re.compile(r"(?<!\d)(?:sh|sz|bj)?(\d{6})(?!\d)", re.IGNORECASE)


def _block_file_path():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", "blocklist_dividendGrowthYearZero.txt")


def _ensure_file():
    path = _block_file_path()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            pass
    return path


def get_blocked_codes():
    """获取息增年为0而被屏蔽的股票代码列表；文件不存在时自动创建。"""
    _ensure_file()
    with _BLOCK_LOCK:
        codes = []
        with open(_block_file_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].split("//", 1)[0].strip()
                if not line:
                    continue
                match = _CODE_PATTERN.search(line)
                if match:
                    codes.append(match.group(1))
        return codes


def add_blocked(code, name=""):
    """记录一只息增年为0的股票到屏蔽文件；已存在时不重复添加。"""
    path = _ensure_file()
    with _BLOCK_LOCK:
        existing = set()
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].split("//", 1)[0].strip()
                if not line:
                    continue
                match = _CODE_PATTERN.search(line)
                if match:
                    existing.add(match.group(1))
        if code in existing:
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{code} {name}".rstrip() + "\n")
