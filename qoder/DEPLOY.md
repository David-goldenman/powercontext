# 从干净环境启动

需要 Node.js 20+、npm，以及已运行的 Docker Engine / Docker Compose。
数据库配置依据 [OceanBase 官方镜像说明](https://github.com/oceanbase/docker-images/blob/main/seekdb/README.md)。
本项目将已验证的官方 seekdb 1.4.0.0 镜像固定到 digest，以免 latest 更新改变环境。

在 `qoder/` 目录执行：

```sh
docker compose -p evidence-desk up -d --wait
npm ci
```

复制 `.env.example` 为 `.env`，把 `DB_PORT=2896` 改为 `DB_PORT=2936`。
其余默认值对应 Compose 的本地隔离数据库。

```sh
npm run seed
npm test
npm start
```

打开 <http://127.0.0.1:3210/app> 体验真实数据库应用；展示页为
<http://127.0.0.1:3210/>。如果已有本地 seekdb，可以直接在 `.env`
配置已有实例，不必启动 Compose。

PowerShell 可用 `Copy-Item .env.example .env` 复制配置。只临时切换测试实例时可用：

```powershell
$env:DB_PORT = '2936'
npm test
```

Linux/macOS 对应 `DB_PORT=2936 npm test`。Linux 用户若未配置 Docker socket
访问权限，可以按本机 Docker 安装方式使用管理员执行 Compose；无需改变 socket 权限。

测试和演示数据都放在 `qoder_evidence` 数据库。容器重启后数据保留在
`evidence-desk_evidence-seekdb` 命名卷。结束体验可以执行
`docker compose -p evidence-desk stop`，该命令保留数据。

Compose 文件仅向回环地址开放端口。该方案用于本地评审和单人体验；
Qoder Sites 发布的是不含凭据的静态展示页，真实数据库应用在本地运行。
