import "./theme/theme.css";
import "./theme/base.css";
import React from "react";
import { createRoot } from "react-dom/client";
import * as api from "../api";
import { useStore } from "../store";
import { connectEvents } from "../ws";
import { App } from "./App";
import { legacyPath } from "./legacyPath";
import { applyTheme, cachedLook, DEFAULT_LOOK, lookOf } from "./theme/applyTheme";

// The shipped UI stays the phone experience until the phone wave (spec §2.6).
const phone = matchMedia("(max-width: 767px)").matches;
if (phone) location.replace(legacyPath(location));
else void boot();

// The shipped boot's order (../boot.tsx): public /health, then the theme
// fetch as the session probe, then bootstrap and the event socket.
async function boot() {
  // The cached look before the first await, so the page never paints another.
  applyTheme(cachedLook() ?? DEFAULT_LOOK);
  const health = await api.getHealth().catch(() => null);
  let locked = health?.authenticated === false;
  const onLocked = () => (locked = true);
  if (!locked) {
    window.addEventListener("kraft:unauthenticated", onLocked, { once: true });
    // The theme fetch doubles as the session probe.
    await api
      .getTheme()
      .then((t) => applyTheme(lookOf(t)))
      .catch((e) => {
        if (!locked) console.error("theme fetch failed", e);
      });
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
