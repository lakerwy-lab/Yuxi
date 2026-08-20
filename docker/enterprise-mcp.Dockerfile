FROM python:3.13-slim

COPY --from=m.daocloud.io/ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/

WORKDIR /app/services/enterprise-mcp

ENV TZ=Asia/Shanghai \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DEBIAN_FRONTEND=noninteractive

RUN set -ex \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends --fix-missing curl libpq5 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY services/enterprise-mcp/pyproject.toml /app/services/enterprise-mcp/pyproject.toml
COPY services/enterprise-mcp/uv.lock /app/services/enterprise-mcp/uv.lock

# 第三方依赖层不包含业务源码，并与 API 镜像共享 uv 下载缓存。
RUN --mount=type=cache,id=yuxi-uv-cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-group test --no-install-local \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 源码变化时仅重装两个本地包，不重新下载第三方依赖。
COPY services/enterprise-mcp/src /app/services/enterprise-mcp/src
COPY backend/package /app/backend/package
RUN --mount=type=cache,id=yuxi-uv-cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-group test \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple

CMD ["uv", "run", "--frozen", "--no-sync", "uvicorn", "enterprise_mcp.app:app", "--host", "0.0.0.0", "--port", "8010"]
