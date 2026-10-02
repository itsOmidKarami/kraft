import type { Access } from "../../types";

/** What Settings › Access says on both layouts, and the sign-in card with it.
 *  The perimeter keys auth on the bind, not on the browser: once Kraft
 *  listens on the network, a browser on the machine itself signs in too, and
 *  only the Host check still lets it in at a loopback name. */
export const ACCESS_LEDE = "Auth is off on a 127.0.0.1 bind. Once Kraft listens on the network, every browser signs in, this machine's too.";

/** Under an empty Allowed hosts list on a network bind. */
export const EMPTY_HOSTS = "An empty list refuses every other device (403). This machine still gets in at 127.0.0.1.";

/** The first of this machine's LAN names the server offers that is not on the list yet, or null. */
export const hostSuggestion = (access: Pick<Access, "allowed_hosts" | "lan_hosts">): string | null =>
  (access.lan_hosts ?? []).find((h) => !access.allowed_hosts.includes(h)) ?? null;
