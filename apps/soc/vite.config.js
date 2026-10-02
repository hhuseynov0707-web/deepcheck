import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The SPA calls /api/... on its own origin, always: the session cookie is
// scoped to Path=/api on the SOC's origin and is never sent anywhere else. In
// the Docker image nginx forwards /api/ to soc-api (nginx.conf). `vite dev` and
// `vite preview` have no nginx in front of them, so they forward /api to
// soc-api themselves -- on the host it listens on 8200 (apps/soc-server).
const apiProxy = { "/api": process.env.DEV_API_PROXY || "http://localhost:8200" };

export default defineConfig({
  plugins: [react()],
  server: {
    // Loopback only, like the deployed SOC: it shows every customer's live
    // session, and has no business being reachable from the venue Wi-Fi.
    host: "127.0.0.1",
    port: 3100,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    host: "127.0.0.1",
    port: 3100,
    proxy: apiProxy,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./vitest.setup.js",
    include: ["src/**/*.test.{js,jsx}"],
  },
});
