# 离谱发明工坊 — seekdb 数据层服务
FROM node:24-alpine

WORKDIR /app

# 国内网络环境下 npm 官方源可能很慢，用镜像源加速；可通过 build-arg 覆盖
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}

# seekdb@1.4.0 的 peerDependencies 里 @seekdb/default-embed 声明了 "seekdb": "workspace:*"
# （发包缺陷），标准解析会失败，必须加 --legacy-peer-deps
COPY package.json package-lock.json ./
RUN npm install --legacy-peer-deps --no-audit --no-fund

COPY seekdb.mjs server.mjs seed.mjs ./

ENV PORT=3000 \
    SEEKDB_HOST=fusion-seekdb \
    SEEKDB_PORT=2881 \
    SEEKDB_USER=root \
    SEEKDB_DB=fusion

EXPOSE 3000

# 启动时先确保表存在并灌入种子数据（幂等：已存在则跳过重复写入由 seed 内部逻辑保证）
CMD ["sh", "-c", "node seed.mjs || true; exec node server.mjs"]
