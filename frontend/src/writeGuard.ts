/** The offline refusal both fetch wrappers (`api.ts`'s `req`, `ng/http.ts`'s `request`) share. A leaf module so the
 *  store, which `api.ts` feeds, can bind the predicate without an import cycle. */

/** What a write answers while offline: the banner's promise, kept by the call itself. */
export const OFFLINE_REFUSAL = "Offline: changes are off until it reconnects.";

let offlineNow = () => false;

/** The store hands over its definition of offline once, when it is created. */
export const bindOffline = (read: () => boolean) => {
  offlineNow = read;
};

/** Whether this call is a write that must not be sent: any method but GET, while offline. */
export const refusesWrite = (init?: RequestInit) => (init?.method ?? "GET").toUpperCase() !== "GET" && offlineNow();
