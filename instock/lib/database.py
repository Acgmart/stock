#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

__author__ = 'myh '
__date__ = '2026/5/12 '

db_host = os.environ.get("db_host", "localhost")
db_user = os.environ.get("db_user", "root")
db_password = os.environ.get("db_password", "root")
db_database = os.environ.get("db_database", "instockdb")
db_port = int(os.environ.get("db_port", "3306"))
db_charset = os.environ.get("db_charset", "utf8mb4")

MYSQL_CONN = {
    "host": f"{db_host}:{db_port}",
    "user": db_user,
    "password": db_password,
    "database": db_database,
    "charset": db_charset,
    "max_idle_time": 3600,
    "connect_timeout": 1000,
}
