import React from "react";
import ReactDOM from "react-dom/client";

// IBM Plex Sans, bundled: Vite copies the font files into dist/assets, so the
// page renders the same with no network. Each weight's CSS lists every subset
// with its unicode-range, and the browser fetches only the ones the page uses
// (latin, plus latin-ext for ç ğ ı İ ö ş ü and ₺).
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";

import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
