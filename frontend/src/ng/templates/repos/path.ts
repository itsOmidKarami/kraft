/** A repo's path as a person reads it: the home directory is `~`, so the tail that tells repos apart comes first into view. */
export const homePath = (p: string) => p.replace(/^(?:\/home\/[^/]+|\/Users\/[^/]+|\/root)(?=\/|$)/, "~");
