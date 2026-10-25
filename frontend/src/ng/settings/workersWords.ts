import type { Machine } from "../../types";

export const NO_MACHINES = "No machines yet. Add one in a terminal:";
export const ADD_COMMAND = "kraft admin worker add NAME --ssh HOST";
/** The phone's: it lists machines and changes none. */
export const READ_ONLY_NOTE = "Machines are added and removed in a terminal (kraft admin worker) or through the MCP tools; this page only lists them.";
/** The desktop's, under the card. */
export const TERMINAL_NOTE = "The terminal does the same with kraft admin worker, and both write workers.yaml.";
export const EVERY_REPO = "every repo";
export const NO_GROUPS = "none";

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

/** The groups a machine is in, which a step's `runner` can name. */
export const groupsWords = (m: Machine) => (m.groups.length ? m.groups.join(", ") : NO_GROUPS);

/** The row for this machine's own worker, apart from the machines it can dial. */
export const isLocal = (m: Machine) => m.mode === "local";

/** What the Connects column says: how Kraft reaches the machine. */
export const modeWords = (m: Machine) => (isLocal(m) ? "this machine" : m.mode === "ssh" ? "over ssh" : "dials in");
