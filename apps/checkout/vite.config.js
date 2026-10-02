import react from "@vitejs/plugin-react";
import { defineConfig, searchForWorkspaceRoot } from "vite";

// The page calls two servers on its own origin, and in the image nginx splits
// them (nginx.conf): /api/ goes to the store's server (checkout-api) and
// /deepcheck/api/ goes to the DeepCheck core with the /deepcheck prefix
// stripped, which is where the SDK registers and sends behaviour. `vite dev`
// and `vite preview` have no nginx in front of them, so they do the same split
// here. On the host the store server is on 8100 and the core on 8000.
//
// The key is "/deepcheck/api", not "/deepcheck": Vite matches proxy keys as
// plain prefixes, and "/deepcheck" would also catch /deepcheck.js -- the SDK
// itself, served from publicDir below -- and send it to the core as a 404.
const proxy = {
  "/api": process.env.DEV_CHECKOUT_API_PROXY || "http://localhost:8100",
  "/deepcheck/api": {
    target: process.env.DEV_CORE_PROXY || "http://localhost:8000",
    rewrite: (path) => path.replace(/^\/deepcheck/, ""),
    // As nginx does: the core then answers the SDK's behaviour windows with an
    // acknowledgement only, so the payer's browser never receives a score.
    headers: { "X-DeepCheck-Reply": "ack" },
  },
};

export default defineConfig({
  plugins: [react()],
  // sdk/deepcheck.js is copied into dist/ as /deepcheck.js, so the SDK is
  // served from the store's own origin like any first-party script.
  publicDir: "../../sdk",
  server: {
    host: true,
    port: 3010,
    proxy,
    // pages/Privacy.jsx imports docs/kvkk-aydinlatma.md as text; the dev
    // server refuses files outside its workspace unless they are listed.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../../docs"] },
  },
  preview: { proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./vitest.setup.js",
    include: ["src/**/*.test.{js,jsx}"],
  },
});
