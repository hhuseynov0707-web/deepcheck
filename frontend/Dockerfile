# Multi-stage: build the static bundle with Node, serve it with nginx.
#
# The previous image ran `vite dev` as the production frontend. It worked, but
# a dev server is not a deployment: it rebuilds on the fly, serves unminified
# modules, and is explicitly documented by Vite as not for production use.
# `docker-compose.dev.yml` restores the dev server with hot reload for
# day-to-day work.

# --- build ------------------------------------------------------------------
FROM node:20-alpine AS build

# Keep frontend/ and sdk/ as siblings inside the container so Vite's
# publicDir: "../sdk" (see vite.config.js) resolves the same way it does for
# local development. deepcheck.js is copied into dist/ by the build as a
# result, so nginx serves the SDK from the same origin as the app.
WORKDIR /app/frontend

COPY frontend/package.json ./
RUN npm install

COPY frontend/ .
COPY sdk/ /app/sdk/

# Vite inlines import.meta.env at BUILD time, so the API URL has to be known
# here rather than at container start. docker-compose passes it as a build
# arg; change it and rebuild to point the bundle at a different backend.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_DEMO_VERIFY_CODE=482913
ENV VITE_DEMO_VERIFY_CODE=$VITE_DEMO_VERIFY_CODE

RUN npm run build

# --- serve ------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
