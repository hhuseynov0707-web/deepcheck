FROM node:20-alpine

# Keep frontend/ and sdk/ as siblings inside the container so Vite's
# publicDir: "../sdk" (see vite.config.js) resolves the same way it does
# for local (non-Docker) development.
WORKDIR /app/frontend

COPY frontend/package.json ./
RUN npm install

COPY frontend/ .
COPY sdk/ /app/sdk/

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
