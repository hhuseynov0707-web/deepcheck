FROM node:20-alpine

# Keep frontend/ and sdk/ as siblings inside the container so Vite's
# publicDir: "../sdk" (see vite.config.js) resolves the same way it does
# for local (non-Docker) development.
WORKDIR /app/frontend

# Copy the lockfile too, and install from it: npm ci resolves the exact
# versions recorded there instead of re-resolving the ^ ranges, so the
# image matches a local install.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ .
COPY sdk/ /app/sdk/

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
