// Every request this app makes goes to /api/... on its OWN origin, with the
// browser's cookie for that origin and nothing else.
//
// Why there is no key here. The legacy dashboard kept DASHBOARD_KEY in
// sessionStorage and sent it as X-Dashboard-Key on every poll, so any script
// running in the tab could read it. soc-api (apps/soc-server) now holds the
// key: the analyst types it once into the login form, soc-api compares it and
// answers with an httpOnly, SameSite=Strict cookie scoped to /api that script
// cannot read. This file never stores the key, never puts it in a header and
// never sees the cookie.
//
// Why no configurable API address (the legacy app's VITE_API_URL). The cookie
// belongs to this origin; an API on another origin would not receive it, and
// widening the cookie to make that work would be the opposite of the point.
// nginx (nginx.conf) and Vite's dev proxy (vite.config.js) forward /api/.

export const SAME_ORIGIN = Object.freeze({ credentials: "same-origin" });

const JSON_ACCEPT = Object.freeze({ Accept: "application/json" });

// The login form's and the dashboard's words for what went wrong, in one
// place so the two screens cannot describe the same failure differently.
export const MESSAGES = Object.freeze({
  // Kept from the legacy key prompt: a rejected key reads the same as before.
  invalidKey: "Yetkisiz erişim - pano erişim anahtarı geçersiz",
  rateLimited: "Çok fazla giriş denemesi - bir dakika bekleyip yeniden deneyin",
  loginFailed: "Giriş şu anda yapılamıyor - SOC sunucusu hata döndürdü",
  // A rejected fetch() carries the BROWSER's own English message ("Failed to
  // fetch"); it is never shown. This is what a rejected fetch actually means.
  unreachable: "Sunucuya ulaşılamadı - bağlantıyı ve sunucunun çalıştığını kontrol edin",
  sessionEnded: "SOC oturumunuz sona erdi ya da geçersiz - devam etmek için yeniden giriş yapın.",
  sessionCheckFailed: "SOC sunucusu oturumu doğrulayamadı - giriş yapmayı deneyin.",
  loggedOut: "Oturum kapatıldı.",
  logoutUnconfirmed:
    "Çıkış isteği sunucuya ulaşmadı. Oturum çerezi bu tarayıcıda, en geç 8 saat sonra kendiliğinden geçersiz olur.",
});

// A 401 from any SOC endpoint: the cookie is missing, expired, tampered with
// or logged out. Never a core error -- soc-api reports those as 502, because a
// 401 here sends the analyst back to the login screen.
export class UnauthorizedError extends Error {
  constructor() {
    super(MESSAGES.sessionEnded);
    this.name = "UnauthorizedError";
  }
}

// Failures this app recognised and described in Turkish.
export class PanoError extends Error {
  constructor(message) {
    super(message);
    this.name = "PanoError";
  }
}

export function apiGet(path) {
  return fetch(path, { ...SAME_ORIGIN, headers: { ...JSON_ACCEPT } });
}

// "authenticated" | "anonymous". Throws only when soc-api could not be reached
// or answered something other than 204 / 401.
export async function checkSession() {
  const res = await apiGet("/api/me");
  if (res.status === 401) return "anonymous";
  if (res.ok) return "authenticated";
  throw new PanoError(MESSAGES.sessionCheckFailed);
}

// Resolves on success; rejects with a PanoError in Turkish otherwise.
export async function login(key) {
  let res;
  try {
    res = await fetch("/api/login", {
      ...SAME_ORIGIN,
      method: "POST",
      headers: { ...JSON_ACCEPT, "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
  } catch {
    throw new PanoError(MESSAGES.unreachable);
  }
  if (res.ok) return;
  if (res.status === 401) throw new PanoError(MESSAGES.invalidKey);
  if (res.status === 429) throw new PanoError(MESSAGES.rateLimited);
  throw new PanoError(MESSAGES.loginFailed);
}

// true when soc-api confirmed the logout. The cookie is httpOnly, so only the
// server can clear it; when the request does not arrive, the caller says so.
export async function logout() {
  try {
    const res = await fetch("/api/logout", { ...SAME_ORIGIN, method: "POST", headers: { ...JSON_ACCEPT } });
    return res.ok;
  } catch {
    return false;
  }
}
