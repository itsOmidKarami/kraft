import { ago } from "../../format";

/** What the About screens say about updating when Kraft is free: one source, imported by the desktop page and the phone screen. */

/** A release marked to install when Kraft is free: by a person (`you`) or by auto-update. */
export interface UpdateMark {
  tag: string;
  by: "you" | "auto";
  at: string;
}
/** How the last unattended update or restart ended. */
export interface UpdateOutcome {
  status: "applied" | "failed" | "refused";
  detail: string;
  from: string | null;
  to: string | null;
  at: string;
}
export interface UpdateState {
  installed: string;
  latest: string | null;
  channel: string;
  behind: boolean | null;
  checked_at: string | null;
  /** The Python this server runs on, "3.14"; absent on an older server. */
  python?: string;
  /** What follows is absent on a server older than update-when-free. */
  /** The newest release within the installed major: `latest` can be a newer major beside it. */
  latest_in_major?: string | null;
  /** `KRAFT_NO_UPDATE_CHECK` is set: Kraft does not look for releases on its own. */
  check_disabled?: boolean;
  /** Which installer made this Kraft: uv, brew, pipx, pip or source. */
  install_kind?: string;
  /** Installed from a checkout: no release to look for or install. */
  dev_install?: boolean;
  restart_kind?: "service" | "detached" | "attached";
  marked?: UpdateMark | null;
  /** What Kraft would do at the next free moment. */
  pending?: { release: string | null; restart: string[] };
  /** Why it has not happened yet. */
  blocked?: string[];
  outcome?: UpdateOutcome | null;
}

/** `GET /update/settings`: `update.yaml`, every key defaulted. */
export interface UpdateSettings {
  auto_update: boolean;
  restart_when_free: boolean;
  channel: string | null;
  allowed_hours: string[];
}
export const NO_SETTINGS: UpdateSettings = { auto_update: false, restart_when_free: false, channel: null, allowed_hours: [] };

/** The saved settings from whatever the server answered: a server without the file's route answers something else, and reads as everything off. */
export function readSettings(body: unknown): UpdateSettings {
  const b = (body ?? {}) as Partial<UpdateSettings>;
  return {
    auto_update: b.auto_update === true,
    restart_when_free: b.restart_when_free === true,
    channel: typeof b.channel === "string" ? b.channel : null,
    allowed_hours: Array.isArray(b.allowed_hours) ? b.allowed_hours.filter((h): h is string => typeof h === "string") : [],
  };
}

export const AUTO_UPDATE_HINT = "Mark each new minor or patch release on this channel. A new major version waits for you to mark it.";
export const RESTART_WHEN_FREE_HINT = "Restart on its own when a dev install or a settings change needs one. A release you marked always restarts.";
export const ALLOWED_HOURS_HINT = "Updates and restarts happen only inside these hours, in the server's local time, and only when nothing is running. None means any hour.";
export const CHECK_OFF_NOTE = "Kraft is not checking for releases on its own (KRAFT_NO_UPDATE_CHECK is set), so auto-update finds none. Check now still asks.";
export const ATTACHED_NOTE = "This server was started in a terminal, so it cannot restart itself. It shows the update here; restart it in that terminal.";

/** A release a person can mark: the newest within the installed major, and a newer major beside it. */
export interface Offer {
  tag: string;
  major: boolean;
}

const bare = (tag: string) => tag.replace(/^v/, "");

/** Whether Kraft can install a release into this server: a release install made with uv. A server that does not say (older) is taken at its word, as before: nothing here offers it. */
export function canMark(u: UpdateState): boolean {
  return u.install_kind === "uv" && u.dev_install === false;
}

/** The releases to offer, none when nothing newer exists or one is already marked. */
export function offers(u: UpdateState): Offer[] {
  if (!canMark(u) || u.behind !== true || !u.latest || u.marked) return [];
  const inMajor = u.latest_in_major && bare(u.latest_in_major) !== u.installed ? u.latest_in_major : null;
  if (inMajor && inMajor !== u.latest) return [{ tag: inMajor, major: false }, { tag: u.latest, major: true }];
  return [{ tag: inMajor ?? u.latest, major: false }];
}

/** Why a release cannot be marked here, when one is available and Kraft cannot take it. */
export function cannotMark(u: UpdateState): string | null {
  if (u.behind !== true || u.install_kind === undefined || canMark(u) || u.dev_install) return null;
  return u.install_kind === "brew" ? "Installed with Homebrew: update with brew upgrade kraft." : `Installed with ${u.install_kind}: Kraft does not update it for you.`;
}

/** What is waiting, and why it has not happened: one line, or null when nothing is. */
export function waitingLine(u: UpdateState): string | null {
  const release = u.pending?.release;
  const restart = u.pending?.restart ?? [];
  if (!release && !restart.length) return null;
  const what = release
    ? `${release} is marked${u.marked?.by === "auto" ? " by auto-update" : ""}: it installs, then Kraft restarts`
    : `A restart is waiting (${restart[0]})`;
  const why = u.blocked?.[0];
  return `${what}. ${why ? `Not yet: ${why}.` : "It happens at the next free moment."}`;
}

/** How the last attempt ended. */
export function outcomeLine(u: UpdateState): string | null {
  const o = u.outcome;
  if (!o) return null;
  const when = ago(o.at);
  if (o.status === "applied") return `Last update: applied${o.from && o.to ? ` ${o.from} → ${o.to}` : ""}, ${when}.`;
  if (o.status === "failed") return `Last update failed, ${when}: ${o.detail}`;
  return `Last update was not taken, ${when}: ${o.detail}`;
}

/** `01:00-06:00` shown as `01:00–06:00`. */
export const showRange = (r: string) => r.replace("-", "–");

const HOURS = /^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/;

/** The ranges typed as a list, split on commas, or a refusal in words. */
export function parseHours(text: string): { ranges: string[] } | { error: string } {
  const ranges = text.split(/[,\n]/).map((r) => r.trim().replace(/[–—]/, "-")).filter(Boolean);
  const bad = ranges.find((r) => !HOURS.test(r) || r.slice(0, 5) === r.slice(6));
  return bad ? { error: `${bad} is not a range like 01:00-06:00.` } : { ranges };
}
