# 高股息列表
一个精简版 InStock Web 应用，仅保留高股息列表相关功能。
项目仅考虑沪深主板股票。
原仓库地址：https://github.com/myhhub/stock

## 功能
- 读取 `instock/config/stocklist.txt` 中的股票代码。
- 拉取并缓存行情价格。
- 前端展示高股息组合核心参数、过滤器，用于选股和趋势分析。
- 优先请求屏蔽相关数据（行业、收益、息增年），被屏蔽的股票不再请求其余数据。

## 配置股票列表
instock/config/stocklist.txt
每行一个股票代码，可带名称：
600900 长江电力
包含沪深主板全部约3200只股票。

instock/config/followlist.txt
点击股票名称可添加关注，没有followlist.txt时自动创建。

instock/config/blocklist_industry.txt
每行一个需要屏蔽的申万二级行业，一般是不能出口的下行行业。
对于银行业，可以直接用银行ETF易方达。

instock/config/blocklist_industryStocks.txt
屏蔽行业对应的股票缓存，避免重复判断行业。
如需解除屏蔽，可手动编辑blocklist_industry.txt后删除股票缓存。

instock/config/blocklist_dividendGrowthYearZero.txt
息增年为0的股票自动记录到此文件并屏蔽。

instock/config/blocklist_dividendYieldBelowOne.txt
股息率低于1%的股票自动记录到此文件并屏蔽。

instock/config/blocklist_negativeEps.txt
收益（上年年报稀释每股收益）为负的股票自动记录到此文件并屏蔽。

## 参数解释
名称：派息时显示为绿色，财报时显示为红色，点击股票名可添加/删除关注。
扣非：最新季报的扣非净利润同比增长，作为业绩参考。
收益（隐藏）：上一个财年年报的稀释每股收益。
派息（隐藏）：上一个财年的所有派息总和，包括计划中但是未执行的。
昨日收盘价（隐藏）：昨日收盘价，判断涨跌。
股价（隐藏）：盘中实时价格或收盘价。
股息率：上一个财年的派息/当前股价，作为股东回报参考。
  默认最低3%股息率，可在页面调整。
息增年：派息持续增加的年份，0年即上一个财年的派息额小于上上年。
FCF（隐藏）：窄口径自由现金流。
FCF/股息：窄口径FCF/股息，评估自由现金流对股息的覆盖率。
FCF/股价：窄口径FCF/股价，评估赚钱能力。
MA120:最新股价相对于日线MA120的涨跌，评估中长线估值水平。
  买点提示红色背景：
    最新股价相对昨日收盘价下跌。
    最新股价位于MA120相对位置0%以下。
    昨日收盘价/MA120相对位置除以10%后取整 - 最新股价/MA120相对位置除以10%后取整 > 0
  卖点提示绿色背景：
    最新股价相对昨日收盘价上涨。
    最新股价位于MA120相对位置0%以上。
    昨日收盘价/MA120相对位置除以10%后取整 - 最新股价/MA120相对位置除以10%后取整 < 0
行业：申万二级行业，作为行业板块参考。
市值：流通市值，单位亿，作为风险参考，一般来说低市值企业波动率更大。
反弹：最新股价相对于最近20个交易日的最低价的涨幅，评估月内波动。
回落：最新股价相对于最近20个交易日的最高价的跌幅，评估月内波动。
派息历史（隐藏）：分红配送相关的公告变化。

## 过滤器解释
过滤扣非：不显示扣非低于-10%的股票。
关注：只显示关注列表中的股票，点击股票名可添加/删除关注。
最低股息率%：不显示股息率低于该百分比的股票。
最低市值：不显示流通市值低于该值的股票，单位亿。
最低息增年：派息不下降的年份持续最少多少年。

# 准备好Docker部署环境
## 终端开启代理（可选）
在～/.zshrc文件中添加一行：
alias daili='export http_proxy=http://127.0.0.1:7897; export https_proxy=http://127.0.0.1:7897; export all_proxy=socks5://127.0.0.1:7897'
端口号改成自己的软件的端口号，运行新的终端，执行daili命令即可。验证是否开启成功：
curl -I https://www.google.com

## 安装Docker app
brew install --cask docker
这个时候程序目录出现Docker图标，可以手动运行，也可以代码运行：
open -a Docker
点击菜单栏小图标可以唤起Docker面板

## Docker app设置
General：开启自动启动、关闭启动时打开Dashboard

## 创建本地常驻文件
mkdir -p "$HOME/instock-data/mariadb/data"

# 创建Docker容器
## Create Docker network
docker network create InStockService

## Start MariaDB
docker run -d --name InStockDbService \
  --network InStockService \
  -v "$HOME/instock-data/mariadb/data:/var/lib/mysql" \
  -e MARIADB_ROOT_PASSWORD=root \
  mariadb:latest

当出现联网问题时可选：
拉取Mariadb失败时需要添加专用的源，用完去掉，Docker Desktop → Settings → Docker Engine：
"registry-mirrors": [
    "https://docker.m.daocloud.io"
  ]

## 给本地仓库脚本可执行权限
ls -l /Volumes/Game/Git/stock/instock/bin/*.sh
chmod +x /Volumes/Game/Git/stock/instock/bin/run_web.sh
终端输入“chmod +x ”然后把文件拖到终端里生成正确的地址。

## Start InStock
docker run -dit --name InStock \
  --network InStockService \
  -p 9988:9988 \
  -v /Volumes/Game/Git/stock:/data/InStock \
  -e db_host=InStockDbService \
  mayanghua/instock:latest
其中“/Volumes/Game/Git/stock”指仓库地址，根据实际情况修改。
“/data/InStock”指容器中的地址。

# 运行项目
浏览器打开：http://localhost:9988/

## 查看容器日志
docker logs -f InStock
docker logs -f InStockDbService

## 重启docker
docker restart InStock

## 看最近日志
docker logs --tail 200 InStock
docker logs --tail 200 InStockDbService

## 自动启动容器
配合Docker自动启动实现开机后自动运行
docker update --restart=always InStock
docker update --restart=always InStockDbService

# 高股息持仓心得
首先必须要介绍高股息之家，关注微信公众号“高股息之家”查看历史消息。
六大原则：
（一）安全边际，股息率需大于无风险利率。
（二）季报原则，扣非不能低于-10%，需保持正常经营。
（三）不择时，股息率自带择时。
（四）低卖高卖，非长期持股，四进三出。
（五）分散持股。
（六）单因子，唯股息论。
十六字诀：
“持股守息，等待过激；若无过激，持股守息”。
一个底线：
如果股息率组合连续两年收益为负，不管负得多么微小，我都会放弃。
因为作为一名全职投资者，我无法接受自己两年没有收入。

介绍本工具的研究思路：
选股：股息率大于3%的企业，每年更新一次列表，A股一般是4月底截止发布完年报。
要综合关注业绩、派息、窄口径自由现金流、日MA120位置、行业轮动、市值区间。
季报扣非低于-10%属于暴雷，需要避开。
派息表示对股东的回报意愿，不是越高越好，也要关注可持续性。
企业财报中的每股收益一般对应企业常态化的盈利能力，基于约定派息比例派息。
自由现金流FCF表示当下的盈利能力，能覆盖派息更好，盈利越强增加派息空间越多。
表中的FCF为窄口径FCF，即财报中的现金流量表中的“经营活动产生的现金流量净额”-“购建固定资产、无形资产和其他长期资产支付的现金”。
  窄口径FCF只扣除刚性支出，通常用于制造业，对于金融行业则使用了稀释每股收益作为替代。
  对于财报中有特殊情况的，可以手动计算窄口径FCF值填入。
日MA120是长线持股关注的重要指标。低于/高于日MA120线10%、摸线，等可能会被判断为买卖点。
  很多科技股长期高位运行，即便跌破MA120，由于没有派息回购，无法确认底部。
  市场缺少流动性时，唯一的潜在买方就是公司自己。
  MA120股指体系，只适用于高股息。因为高股息是一种处境，意味着这家公司没有故事可讲，只能靠真金白银吸引股东。
  对于科技股来说，就是讲故事、吹泡沫，完全可以跌90%，只要不回购不派息就一直跌，跌到有人买为止。
行业轮动，对应着资金对不同板块的偏好。
市值区间反应一定风险偏好，小市值企业波动偏大，可能面临巨大回撤。
