import type { Machine } from "../../types";

export const NO_MACHINES = "No machines yet. Add one in a terminal:";
export const ADD_COMMAND = "kraft admin worker add NAME --ssh HOST";
/** The phone's: it lists machines and changes none. */
export const READ_ONLY_NOTE = "Add and edit machines in Settings on a wider screen, or with kraft admin worker in a terminal.";
export const EVERY_REPO = "every repo";

/** The State column's word. Never colour alone: a badge carries it. */
export const stateWord = (m: Machine): "online" | "offline" | "disabled" => (!m.enabled ? "disabled" : m.online ? "online" : "offline");

/** What goes with `stateWord`: why it is offline, or that a disabled machine takes no new steps. */
export const stateReason = (m: Machine) => {
  if (m.enabled) return m.online ? "" : (m.reason ?? "");
  return m.online ? "takes no new steps" : `offline: ${m.reason ?? "no connection"}`;
};

/** What `give_up_after` means for a machine, in words. */
export const giveUpWords = (after: string) => (after === "never" ? "never gives up" : `gives up after ${after}`);

/** Slots in use, as "1 of 2", with "· machine's limit" when the machine's own limit is the lower;
 * the local worker without a limit: "1 · no limit". */
export const slotsWords = (m: Machine) => {
  if (m.capacity === null) return `${m.slots_used} · no limit`;
  const of = `${m.slots_used} of ${m.capacity}`;
  return m.capacity_source === "machine" ? `${of} · machine's limit` : of;
};

/** The row for this machine's own worker, apart from the machines it can dial. */
export const isLocal = (m: Machine) => m.mode === "local";

/** What the Connects column says: how Kraft reaches the machine. */
export const modeWords = (m: Machine) => (isLocal(m) ? "this machine" : m.mode === "ssh" ? "over ssh" : "dials in");

/** The presets of Give up after: `workers.yaml`'s value, then how it reads. */
export const GIVE_UP_PRESETS = [
  { value: "30m", label: "30 minutes" },
  { value: "1h", label: "1 hour" },
  { value: "2h", label: "2 hours" },
  { value: "8h", label: "8 hours" },
  { value: "1d", label: "1 day" },
  { value: "never", label: "never" },
];
export const DEFAULT_GIVE_UP = "2h";
