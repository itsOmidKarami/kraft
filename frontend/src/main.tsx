import React from "react";
import { createRoot } from "react-dom/client";
import { useStore } from "./store";
import * as api from "./api";
import { applyDensity, applyTheme } from "./theme";
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

async function boot() {
  try {
    const theme = await api.getTheme();
    applyTheme(theme.palette, theme.mode);
    applyDensity(theme.density);
  } catch (e) {
    // Nocturne dark (nocturne.css's unscoped :root) is already the page's
    // look with no attributes set — a failed fetch here just means the
    // saved choice doesn't apply yet, not a broken page.
    console.error("theme fetch failed", e);
  }
  try {
    await useStore.getState().bootstrap();
  } catch (e) {
    console.error("bootstrap failed", e);
  }
  connectEvents();
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void boot();
