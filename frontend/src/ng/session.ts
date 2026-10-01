import * as api from "../api";
import { useStore } from "../store";
import { connectEvents } from "../ws";
import { onApplyFrame, watchApply } from "./apply/store";
import { applyTheme, lookOf } from "./theme/applyTheme";

let stopEvents: (() => void) | null = null;
let stopApply: (() => void) | null = null;

/** One event socket at a time: a sign-in after a 401 must not leave the old one running. */
export function startEvents() {
  stopEvents?.();
  stopApply?.();
  stopEvents = connectEvents({ onLive: onApplyFrame });
  stopApply = watchApply();
}

/** The boot probe again, after a sign-in: theme (which doubles as the session check), data, socket. */
export async function resumeSession() {
  applyTheme(lookOf(await api.getTheme()));
  await useStore.getState().bootstrap();
  startEvents();
}
