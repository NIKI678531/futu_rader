FROM node:22.22.0-bookworm-slim AS build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ /src/frontend/
COPY design/ /src/design/
ARG VITE_API_BASE
ENV VITE_API_BASE=${VITE_API_BASE}
RUN test "$VITE_API_BASE" = "/api/v1" \
    && npm run build

FROM nginxinc/nginx-unprivileged:1.30.0-alpine
ENV NGINX_ENVSUBST_FILTER=^API_UPSTREAM$ \
    TZ=Asia/Hong_Kong
USER root
RUN apk add --no-cache tzdata \
    && rm -f /etc/nginx/conf.d/default.conf \
    && chmod 1777 /etc/nginx/conf.d /tmp
COPY frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY frontend/docker-entrypoint.d/25-require-rendered-config.sh /docker-entrypoint.d/
RUN chmod 755 /docker-entrypoint.d/25-require-rendered-config.sh
COPY --from=build /src/frontend/dist/ /usr/share/nginx/html/
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1