// The one place the browser learns where the DeepCheck API lives. Demo.jsx and
// Dashboard.jsx both import it, so the two pages cannot disagree.
//
// EMPTY (the default) means SAME ORIGIN: the page calls /api/... on whatever
// host and port it was itself loaded from, and the server in front of it
// forwards /api/ to the backend -- nginx in the Docker image (nginx.conf),
// Vite's proxy under `npm run dev` / `npm run preview` (vite.config.js). No
// address is compiled into the bundle, so the same build works at
// http://localhost:3000, at the presenter's LAN address on venue Wi-Fi, and at
// whatever new address a hotspot hands out, with no rebuild.
//
// `??`, not `||`. The old `import.meta.env.VITE_API_URL || "http://localhost:8000"`
// turned an EMPTY value back into localhost, so a same-origin build was
// impossible -- and "localhost" in a bundle is the VISITOR'S device: a second
// laptop or a phone given that bundle talks to itself. Set VITE_API_URL only to
// point the bundle at an API on a DIFFERENT origin; the backend's CORS_ORIGINS
// and API_BIND_ADDR then have to allow it (.env.example).
//
// Trailing slashes are stripped because every caller appends "/api/...". A
// value of "/" would otherwise produce "//api/...", which the browser reads as
// a protocol-relative URL for a host named "api", and "http://host:8000/"
// would request "//api/..." from the backend, a path that does not exist.
export const API_URL = (import.meta.env.VITE_API_URL ?? "").trim().replace(/\/+$/, "");
