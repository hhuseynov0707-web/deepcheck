import react from "@vitejs/plugin-react";
import { defineConfig, searchForWorkspaceRoot } from "vite";

export default defineConfig({
  plugins: [react()],
  publicDir: "../sdk",
  server: {
    host: true,
    port: 3000,
    // pages/KvkkNotice.jsx imports ../docs/kvkk-aydinlatma.md as text, one
    // source for the document and the /kvkk page. The dev server refuses
    // files outside its workspace unless they are listed here.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../docs"] },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./vitest.setup.js",
    include: ["src/**/*.test.{js,jsx}"],
  },
});
