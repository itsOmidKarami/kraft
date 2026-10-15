/** What Archived is, in one source for the desktop page, the phone's and the board's Done link. */
const afterDays = (days: number | null | undefined) => (days ? days : null);

/** The Done group's note beside its link to Archived; null when the policy archives nothing on its own (`archive.after_days` unset or 0). */
export const autoArchiveNote = (days: number | null | undefined) => (afterDays(days) ? `auto-archive after ${days} days` : null);

/** What Archived holds and when items arrive (Policy › Housekeeping › archive after). */
export const archivedLede = (days: number | null | undefined) =>
  `Items you archive land here${afterDays(days) ? `, and so do done and cancelled ones ${days} days after they finish` : ""}. Open one to restore it.`;

export const ARCHIVED_READ_ERROR = (why: string) => `The archived items could not be read. ${why}`;
