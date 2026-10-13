# Builds the web app and bakes the static files into the Caddy image. Build context: the repository root.
#   docker build -f deploy/caddy/Dockerfile .
FROM node:22-alpine AS web-build
ENV npm_config_store_dir=/pnpm-store CI=true
RUN npm install -g pnpm@10.28.0
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# The browser calls the API on the same origin under /api (Caddy strips the prefix).
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}
RUN pnpm build

FROM caddy:2-alpine
COPY deploy/caddy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/caddy/tls-0.caddy deploy/caddy/tls-1.caddy /etc/caddy/
COPY --from=web-build /web/dist /srv
