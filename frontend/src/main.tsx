import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { useStore } from "./store";
import "./styles.css";
import { connectEvents } from "./ws";

async function boot() {
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
