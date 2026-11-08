import { hostLabel } from "./serverUrl";

// What a window signed in to another machine's Kraft says when it refuses an
// action that reads or acts on this machine. Each names the server, and none
// mentions a token: a signed-in window has none to blame.

export const remoteStart = (base: string): string =>
  `Start Kraft runs on this machine, and you are signed in to ${hostLabel(base)}.`;

export const remoteWorktree = (base: string): string =>
  `An item's worktree is on the server's machine (${hostLabel(base)}). Open it there, or use the web UI.`;

export const remoteReview = (base: string): string =>
  `Review Changes reads the item's worktree, which is on the server's machine (${hostLabel(base)}). Use the web UI to review the diff.`;

export const remoteConfig = (base: string): string =>
  `Config files are on the server's machine (${hostLabel(base)}). Edit them there, or in the web UI's Settings.`;
