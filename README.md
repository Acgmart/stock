# 高股息列表
一个精简版 InStock Web 应用，仅保留高股息列表相关功能。
项目仅考虑沪深主板股票。
原仓库地址：https://github.com/myhhub/stock

## 功能
- 读取 `instock/config/stocklist.txt` 中的股票代码。
- 拉取并缓存行情价格。
- 前端展示股息率、股息增长年、FCF/股息、FCF/股价等。

## 配置股票列表
instock/config/stocklist.txt
每行一个股票代码，可带名称：
600900 长江电力
已经配置了300多只股息率大于3%的股票，每年5月1日更新。

# 终端开启代理（可选）
在～/.zshrc文件中添加一行：
alias daili='export http_proxy=http://127.0.0.1:7897; export https_proxy=http://127.0.0.1:7897; export all_proxy=socks5://127.0.0.1:7897'
端口号改成自己的软件的端口号，运行新的终端，执行daili命令即可。验证是否开启成功：
curl -I https://www.google.com

# 安装Docker app
brew install --cask docker
这个时候程序目录出现Docker图标，可以手动运行，也可以代码运行：
open -a Docker
点击菜单栏小图标可以唤起Docker面板

# Docker app设置
General：开启自动启动、关闭启动时打开Dashboard

# 创建本地常驻文件
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

## Create Database
等待 MariaDB 启动完成后，创建数据库：
docker exec InStockDbService mariadb -h 127.0.0.1 -u root -proot -e "CREATE DATABASE IF NOT EXISTS instockdb CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

查询当前的数据库列表：
docker exec InStockDbService mariadb -h 127.0.0.1 -u root -proot -e "SHOW DATABASES;"

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
（二）季报原则，扣非不能低于-10%，需保持在正常波动范围。
（三）不择时，股息率自带择时。
（四）低卖高卖，非长期持股。
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
  窄口径FCF只扣除刚性支出，通常用于制造业，对于金融行业则用每股收益评估更合理。
  对于财报中有特殊情况的，可以手动计算窄口径FCF值填入。
日MA120是长线持股关注的重要指标。低于/高于日MA120线10%、摸线，等可能会被判断为买卖点。
行业轮动，对应着资金对不同板块的偏好。
市值区间反应一定风险偏好，小市值企业波动偏大，可能面临巨大回撤。
