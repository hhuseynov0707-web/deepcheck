import react from "@vitejs/plugin-react";
import { defineConfig, searchForWorkspaceRoot } from "vite";

// The bundle calls the API on its own origin by default (src/apiBase.js), and
// in the Docker image nginx forwards /api/ to the backend. `vite dev` and
// `vite preview` have no nginx in front of them, so without this they would
// answer /api/... themselves -- a 404, or index.html where JSON was expected.
// DEV_API_PROXY is set by docker-compose.dev.yml, where the backend is the
// compose service "backend"; on the host the backend is on localhost:8000.
const apiProxy = { "/api": process.env.DEV_API_PROXY || "http://localhost:8000" };

export default defineConfig({
  plugins: [react()],
  publicDir: "../sdk",
  server: {
    host: true,
    port: 3000,
    proxy: apiProxy,
    // pages/KvkkNotice.jsx imports ../docs/kvkk-aydinlatma.md as text, one
    // source for the document and the /kvkk page. The dev server refuses
    // files outside its workspace unless they are listed here.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../docs"] },
  },
  preview: {
    proxy: apiProxy,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./vitest.setup.js",
    include: ["src/**/*.test.{js,jsx}"],
  },
});
