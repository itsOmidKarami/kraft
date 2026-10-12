export const RESTRICTED = "Kraft is read-only in Restricted Mode: trust this workspace to act.";
export const VERSION_REFUSAL = "Kraft is read-only: this server's version does not work with this extension. Hover Kraft in the status bar to see which one to update.";

export interface ReadOnlyState {
  trusted: boolean;
  // Why this server's version is refused, or null when it is not.
  version: string | null;
}

// Restricted Mode first: trusting the workspace is the user's own next step.
export function refusal(s: ReadOnlyState): string | null {
  if (!s.trusted) return RESTRICTED;
  if (s.version) return VERSION_REFUSAL;
  return null;
}

export function tooltip(s: ReadOnlyState): string | undefined {
  const lines = [s.trusted ? null : "Restricted Mode: trust this workspace to approve, reject or act on items.", s.version];
  const shown = lines.filter((l): l is string => !!l);
  return shown.length ? shown.join("\n\n") : undefined;
}

// Calls `register` once: now when trusted, else on the first grant.
export function whenTrusted(
  trusted: boolean,
  onGrant: (listener: () => void) => { dispose(): void },
  register: () => void,
): { dispose(): void } | undefined {
  if (trusted) {
    register();
    return undefined;
  }
  let done = false;
  const sub = onGrant(() => {
    if (done) return;
    done = true;
    sub.dispose();
    register();
  });
  return sub;
}
