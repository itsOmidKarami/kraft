import type { Machine } from "../../types";

export const NO_MACHINES = "No machines yet. Add one in a terminal:";
export const ADD_COMMAND = "kraft admin worker add NAME --ssh HOST";
export const READ_ONLY_NOTE = "Machines are added and removed in a terminal (kraft admin worker) or through the MCP tools; this page only lists them.";
export const EVERY_REPO = "every repo";

/** `gpu-box`'s state in a sentence: online, or offline and why. */
export const stateWords = (m: Machine) => (m.online ? "online" : m.reason ? `offline: ${m.reason}` : "offline");

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

/** What the Mode column says: `local` reads as the machine Kraft itself runs on. */
export const modeWords = (m: Machine) => (isLocal(m) ? "this machine" : m.mode);
