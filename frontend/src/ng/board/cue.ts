import { CircleAlert, CircleHelp, Clock, Pause, TriangleAlert } from "../icons";
import type { StopCue } from "./rowText";

/** The icon beside a Needs you reason, one per kind of stop; the desktop row and the phone card draw the same. */
export const CUE_ICON: Record<StopCue, typeof Clock> = { failed: CircleAlert, paused: Pause, gate: Clock, question: CircleHelp, limit: TriangleAlert, other: TriangleAlert };
