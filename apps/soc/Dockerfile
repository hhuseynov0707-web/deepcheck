# soc-web: the SOC SPA (apps/soc) built with Node, served by nginx on 3100,
# which also forwards /api/ to soc-api (nginx.conf).
#
# Build context: the REPOSITORY ROOT, as docker-compose.yml builds it
# (`context: .`, `dockerfile: apps/soc/Dockerfile`), like the store's image.
# What the builder receives is narrowed by apps/soc/Dockerfile.dockerignore to
# this folder alone: the SPA shares no files with the rest of the repo at build
# time -- its components are copies (docs/architecture-two-apps.md) and its
# fonts come from npm. (src/parity.test.js reads files outside this folder --
# still the former demo's frontend/, removed 2026-10-02; see its STALE note --
# but tests are not run here.)

# --- build ------------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# `npm ci`: exactly package-lock.json, and a failure if it drifted from
# package.json, so the image is the dependency tree the tests passed on.
COPY apps/soc/package.json apps/soc/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY apps/soc/ .
RUN npm run build

# --- serve ------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY apps/soc/nginx.conf /etc/nginx/conf.d/default.conf
# Not in conf.d/, whose *.conf files nginx loads on their own at http level.
COPY apps/soc/nginx-security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3100

CMD ["nginx", "-g", "daemon off;"]
