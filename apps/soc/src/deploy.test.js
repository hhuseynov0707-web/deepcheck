import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// The image's nginx config is part of the contract (docs/architecture-two-apps.md):
// port 3100, /api/ to soc-api:8200 resolved per request, no access log, and the
// security headers on every response. `nginx -t` is not available where these
// tests run, so the parts that matter are pinned as text.

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf-8");
const conf = read("../nginx.conf");
const snippet = read("../nginx-security-headers.conf");
const dockerfile = read("../Dockerfile");
const dockerignore = read("../Dockerfile.dockerignore");

// Directives only, comments dropped.
const directives = (text) =>
  text
    .split("\n")
    .map((line) => line.replace(/#.*$/, "").trim())
    .filter(Boolean)
    .join("\n");

// `location ... { ... }` blocks; nginx.conf has no nested blocks inside them.
function locations(text) {
  const found = {};
  const re = /location\s+([^{]+?)\s*\{([^}]*)\}/g;
  let match;
  while ((match = re.exec(text))) found[match[1].trim()] = match[2];
  return found;
}

describe("soc-web nginx.conf", () => {
  const body = directives(conf);
  const blocks = locations(body);

  it("listens on 3100 with no access log", () => {
    expect(body).toMatch(/^listen 3100;$/m);
    expect(body).toMatch(/^access_log off;$/m);
  });

  it("forwards /api/ to soc-api through Docker's resolver, looked up per request", () => {
    expect(body).toMatch(/^resolver 127\.0\.0\.11 valid=10s ipv6=off;$/m);
    const api = blocks["/api/"];
    expect(api).toMatch(/set \$soc_api http:\/\/soc-api:8200;/);
    expect(api).toMatch(/proxy_pass \$soc_api;/);
    // The client's X-Forwarded-For is replaced, never appended to.
    expect(api).toMatch(/proxy_set_header X-Forwarded-For \$remote_addr;/);
    expect(api).not.toMatch(/proxy_add_x_forwarded_for/);
    expect(api).toMatch(/add_header Cache-Control "no-store" always;/);
  });

  it("falls back to the SPA and never caches index.html", () => {
    expect(blocks["/"]).toMatch(/try_files \$uri \$uri\/ \/index\.html;/);
    expect(blocks["= /index.html"]).toMatch(/add_header Cache-Control "no-cache";/);
  });

  it("repeats the security headers in every location that sets its own header", () => {
    expect(body).toMatch(/^include \/etc\/nginx\/snippets\/security-headers\.conf;$/m);
    for (const [path, block] of Object.entries(blocks)) {
      if (/add_header/.test(block)) {
        expect(block, path).toMatch(/include \/etc\/nginx\/snippets\/security-headers\.conf;/);
      }
    }
  });

  it("sends the security headers, with a same-origin-only content policy", () => {
    const headers = directives(snippet);
    for (const name of ["X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy"]) {
      expect(headers).toMatch(new RegExp(`^add_header ${name} ".+" always;$`, "m"));
    }
    const csp = /Content-Security-Policy "([^"]+)"/.exec(headers)[1];
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("connect-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).not.toContain("unsafe-eval");
  });

  it("is what the image installs and exposes, built from the repository root", () => {
    // docker-compose.yml builds soc-web with `context: .`; every COPY names
    // its source from the repository root, and the Dockerfile's own ignore
    // file lets only apps/soc through.
    expect(dockerfile).toMatch(/^COPY apps\/soc\/package\.json apps\/soc\/package-lock\.json \.\/$/m);
    expect(dockerfile).toMatch(/^COPY apps\/soc\/nginx\.conf \/etc\/nginx\/conf\.d\/default\.conf$/m);
    expect(dockerfile).toMatch(
      /^COPY apps\/soc\/nginx-security-headers\.conf \/etc\/nginx\/snippets\/security-headers\.conf$/m,
    );
    expect(dockerfile).toMatch(/^EXPOSE 3100$/m);
    expect(dockerfile).toMatch(/^RUN npm ci /m);
    const ignore = directives(dockerignore).split("\n");
    expect(ignore.slice(0, 2)).toEqual(["*", "!apps/soc"]);
    expect(ignore).toContain("**/node_modules");
    expect(ignore).toContain("**/.env");
  });
});
