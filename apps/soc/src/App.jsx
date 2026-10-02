import { useEffect, useState } from "react";

import { checkSession, logout, MESSAGES, PanoError } from "./api.js";
import AppShell from "./components/AppShell.jsx";
import Skeleton from "./components/Skeleton.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Login from "./pages/Login.jsx";

// Which screen the SOC shows, decided by soc-api and nothing else.
//
// On load GET /api/me says whether this browser holds a valid session cookie.
// The cookie is httpOnly, so the page cannot look at it -- and should not try:
// only the server can tell an expired or revoked cookie from a good one. Any
// 401 later, from any poll, comes back here through onUnauthorized and drops
// the analyst on the login screen instead of leaving a dead page polling.
export default function App() {
  // "checking" | "anonymous" | "authenticated"
  const [auth, setAuth] = useState("checking");
  // { tone, text } shown above the login form, or null.
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    let cancelled = false;
    checkSession()
      .then((state) => {
        if (!cancelled) setAuth(state);
      })
      .catch((err) => {
        if (cancelled) return;
        setAuth("anonymous");
        setNotice({ tone: "danger", text: err instanceof PanoError ? err.message : MESSAGES.unreachable });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function handleLoggedIn() {
    setNotice(null);
    setAuth("authenticated");
  }

  function handleUnauthorized() {
    setNotice({ tone: "info", text: MESSAGES.sessionEnded });
    setAuth("anonymous");
  }

  async function handleLogout() {
    const confirmed = await logout();
    // The cookie is httpOnly: if the request did not arrive, it is still in
    // this browser, and the analyst is told so rather than told it is gone.
    setNotice(confirmed ? { tone: "neutral", text: MESSAGES.loggedOut } : { tone: "danger", text: MESSAGES.logoutUnconfirmed });
    setAuth("anonymous");
  }

  if (auth === "authenticated") {
    return <Dashboard onUnauthorized={handleUnauthorized} onLogout={handleLogout} />;
  }

  return (
    <AppShell>
      {auth === "checking" ? (
        <div className="mx-auto w-full max-w-md px-4 py-20" role="status">
          <span className="sr-only">Oturum denetleniyor.</span>
          <Skeleton className="h-72 w-full" rounded="rounded-card" />
        </div>
      ) : (
        <Login onLoggedIn={handleLoggedIn} notice={notice} />
      )}
    </AppShell>
  );
}
