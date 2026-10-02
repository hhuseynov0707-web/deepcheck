# checkout-web: build TechStore's checkout SPA with Node, serve it with nginx.
#
# Build context is the REPOSITORY ROOT (docker-compose.yml: context ".",
# dockerfile apps/checkout/Dockerfile): the build needs sdk/deepcheck.js
# (publicDir) and docs/kvkk-aydinlatma.md (the /gizlilik page) from outside
# this folder.
# Dockerfile.dockerignore next to this file limits the context to those.

# --- build ------------------------------------------------------------------
FROM node:20-alpine AS build

# The repository's layout, recreated: apps/checkout, sdk and docs as siblings
# under /app, so vite.config.js's publicDir "../../sdk" and the page's
# "../../../../docs" import resolve exactly as they do on a developer's
# machine.
WORKDIR /app/apps/checkout

# The lockfile, and `npm ci`: exactly the dependency tree the tests passed on,
# and a failure rather than a silent rewrite if it drifted from package.json.
COPY apps/checkout/package.json apps/checkout/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY apps/checkout/ .
COPY sdk/ /app/sdk/
COPY docs/kvkk-aydinlatma.md /app/docs/kvkk-aydinlatma.md

# Printed in the step-up dialog as the demo's SMS code. Must match the core's
# DEMO_VERIFY_CODE; Vite inlines it at BUILD time, so changing it means a
# rebuild.
ARG VITE_DEMO_VERIFY_CODE=482913
ENV VITE_DEMO_VERIFY_CODE=$VITE_DEMO_VERIFY_CODE

RUN npm run build

# --- serve ------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY apps/checkout/nginx.conf /etc/nginx/conf.d/default.conf
# Included by nginx.conf from each location that sets its own headers. Not in
# conf.d/, whose *.conf files nginx loads on their own at http level.
COPY apps/checkout/nginx-security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/apps/checkout/dist /usr/share/nginx/html

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
