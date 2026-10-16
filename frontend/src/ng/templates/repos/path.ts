/** A repo's path as a person reads it: the home directory is `~`, so the tail that tells repos apart comes first into view. */
export const homePath = (p: string) => p.replace(/^(?:\/home\/[^/]+|\/Users\/[^/]+|\/root)(?=\/|$)/, "~");

/** A long path by its tail, the last two folders after `…/`, where the folders that tell checkouts apart are; a short one is whole. */
export const pathTail = (p: string, max = 40) => {
  const parts = homePath(p).split("/");
  return homePath(p).length <= max || parts.length <= 3 ? homePath(p) : `…/${parts.slice(-2).join("/")}`;
};
