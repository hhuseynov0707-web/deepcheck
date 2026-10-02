import React from "react";
import ReactDOM from "react-dom/client";

// IBM Plex, bundled: Vite copies the font files into dist/assets, so the SOC
// renders identically on a laptop with no network. The full-weight files carry
// every subset with its unicode-range, so the browser fetches latin-ext (ş, ğ,
// ı, İ) only for the characters that need it.
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/700.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";

import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
