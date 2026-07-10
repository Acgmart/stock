# 高股息列表

一个精简版 InStock Web 应用，仅保留高股息列表相关功能。
项目仅考虑沪深主板股票。

## 功能

- 读取 `instock/config/stocklist.txt` 中的股票代码。
- 拉取并缓存行情价格。
- 拉取并缓存东方财富派息明细。
- 支持手动维护仓位与窄口径 FCF。
- 前端展示股息率、股息增长年、FCF/股息、FCF/股价和派息明细。

## 配置

股票池文件：

```text
instock/config/stocklist.txt
```

每行一个股票代码，可带名称：

```text
600900 长江电力
```

东方财富 Cookie 文件可选：

```text
instock/config/eastmoney_cookie.txt
```


# 安装Docker app
brew install --cask docker
这个时候程序目录出现Docker图标，可以手动运行，也可以代码运行：
open -a Docker
点击菜单栏小图标可以唤起Docker面板

# 创建本地常驻文件
mkdir -p "$HOME/instock-data/mariadb/data"
touch "$HOME/instock-data/eastmoneycookie.txt"

# 创建Docker容器
## Create Docker network
docker network create InStockService

## Start MariaDB
docker run -d --name InStockDbService \
  --network InStockService \
  -v "$HOME/instock-data/mariadb/data:/var/lib/mysql" \
  -e MARIADB_ROOT_PASSWORD=root \
  mariadb:latest

拉取Mariadb失败时需要添加专用的源，用完去掉，Docker Desktop → Settings → Docker Engine：
"registry-mirrors": [
    "https://docker.m.daocloud.io"
  ]

## 给本地仓库脚本可执行权限
ls -l /Users/sekia/Desktop/Git/stock/instock/bin/*.sh
chmod +x /Users/sekia/Desktop/Git/stock/instock/bin/run_web.sh

## Start InStock
docker run -dit --name InStock \
  --network InStockService \
  -p 9988:9988 \
  -v /Users/sekia/Desktop/Git/stock:/data/InStock \
  -e db_host=InStockDbService \
  mayanghua/instock:latest

# 运行项目
浏览器打开：http://localhost:9988/

# 查看容器日志
docker logs -f InStock
docker logs -f InStockDbService

# 重启docker
docker restart InStock

# 设东方财富Cookie
1、获取Cookie
    打开浏览器，访问东方财富网行情页面：https://quote.eastmoney.com/center/gridlist.html#hs_a_board
    登录账号（如果有东方财富网账号，建议登录以获取更稳定的Cookie）
    打开开发者工具（F12）：
    切换到Network（网络）选项卡
    刷新页面（按 F5 或点击浏览器刷新按钮）
    选择任意请求：在网络请求列表中，选择任意一个请求（“get？”开头，建议选择URL包含 push2.eastmoney.com 的请求）
    查看Cookie：在请求详情中，找到 Request Headers（请求头）部分，复制完整的 Cookie 值
    保存Cookie：将复制的Cookie值保存下来，稍后使用
2、设置Cookie
    编辑eastmoney_cookie.txt文件，替换Cookie。
3、注意事项
    Cookie有效期：东方财富网的Cookie通常会在一段时间后过期（一般为几天到几周），如突然无法正常工作，可能是Cookie过期了，需要重新获取并设置
    定期更新：建议每隔一段时间（如每周）更新一次Cookie，以确保爬取的稳定性
    多账号轮换：如果有多个东方财富网账号，可以轮换使用不同账号的Cookie，进一步降低被限制的风险

# 看最近日志
docker logs --tail 200 InStock
docker logs --tail 200 InStockDbService

# 自动启动容器
docker update --restart=always InStock
docker update --restart=always InStockDbService