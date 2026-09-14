import React from "react";
import { createRoot } from "react-dom/client";
import { useStore } from "./store";
import * as api from "./api";
import { applyDensity, applyTheme, savedTheme } from "./theme";
import "./nocturne.css";
import "./palettes.css";
// styles.css must be imported before App: ES imports execute depth-first in
// source order, and App's import graph pulls in every views/**/*.css page
// stylesheet. Importing styles.css after App made it the last stylesheet
// bundled, so it won a tie against any page rule of equal specificity —
// 209 page rules were silently inert. Kraft-9fj8.
import "./styles.css";
import { App } from "./App";
import { connectEvents } from "./ws";

// The last theme this browser used, before anything awaits (W1.3): the saved
// one from the server is a round trip away, and until it lands the page
// would paint Nocturne dark and then flash to light.
const saved = savedTheme();
if (saved) applyTheme(saved.palette, saved.mode);

async function boot() {
  // Kraft-yx79s: a server whose public /health says this browser has no
  // session answers the question without a 401, and the probe below is
  // skipped. A server that does not send `authenticated` keeps the probe.
  const health = await api.getHealth().catch(() => null);
  let locked = health?.authenticated === false;
  // /api/theme doubles as the session check (W8.7): on a locked instance it is
  // the one request that comes back 401, and nothing else is asked until a
  // login -- bootstrap, the event socket and every view's own fetch would
  // each add their own 401 to the login screen.
  const onLocked = () => (locked = true);
  if (!locked) {
    window.addEventListener("kraft:unauthenticated", onLocked, { once: true });
    try {
      const theme = await api.getTheme();
      applyTheme(theme.palette, theme.mode);
      applyDensity(theme.density);
    } catch (e) {
      // Nocturne dark (nocturne.css's unscoped :root) is already the page's
      // look with no attributes set — a failed fetch here just means the
      // saved choice doesn't apply yet, not a broken page.
      if (!locked) console.error("theme fetch failed", e);
    }
    window.removeEventListener("kraft:unauthenticated", onLocked);
  }
  if (!locked) {
    try {
      await useStore.getState().bootstrap();
    } catch (e) {
      console.error("bootstrap failed", e);
    }
    connectEvents();
  }
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App initiallyLocked={locked} />
    </React.StrictMode>,
  );
}

void boot();
