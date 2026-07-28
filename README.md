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

## Create Database
等待 MariaDB 启动完成后，创建数据库：
docker exec InStockDbService mariadb -u root -proot -e "CREATE DATABASE IF NOT EXISTS instockdb CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

出现联网问题或者代理问题：
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

# 查看容器日志
docker logs -f InStock
docker logs -f InStockDbService

# 重启docker
docker restart InStock

# 看最近日志
docker logs --tail 200 InStock
docker logs --tail 200 InStockDbService

# 自动启动容器
docker update --restart=always InStock
docker update --restart=always InStockDbService