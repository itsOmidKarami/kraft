/** The words for a disabled control, one source for the desktop and phone trees. */
export const NOTHING_TO_PUBLISH = "nothing to publish";
export const ADD_TITLE = "Add a title to create it.";
export const fixProblems = (n: number) => `Fix the ${n} ${n === 1 ? "problem" : "problems"} first.`;
/** A component an extending chain inherits from its parent: its structure is the parent's. */
export const INHERITED = (parent: string) => `Inherited from ${parent}: change it in YAML, or detach the chain.`;
export const CHAIN_IN_USE = (repos: string[]) => `Can't delete: ${repos.join(", ")} default to it.`;
export const WEBHOOK_OFF = (urlSet: boolean, typing = false) => (urlSet ? "Turn on the webhook first." : typing ? "Press Enter to save the URL first." : "Set a webhook URL first.");
export const CHOOSE_REPO = "Choose a repo to create it in.";
export const COMMENT_NEEDS_TEXT = "A comment needs some text.";
export const ADD_TO_SUBMIT = "Add a comment or a note first.";
export const ADD_NOTE = "Write what needs to change first.";
export const WRITE_FIRST = "Write something first.";
export const ARCHIVE_ONLY_ENDED = "Only done or cancelled items can be archived.";
/** A refused move, naming the node once: the server's own "can't move X:" lead
 * goes, and a reason that opens with the node reads "it …". */
export function cantMove(name: string, why: string, path = name): string {
  const rest = why.replace(/^can't move [^:]*: /i, "");
  const own = [path, name].find((p) => rest.startsWith(`${p} `));
  return `Can't move ${name}: ${own ? `it${rest.slice(own.length)}` : rest}`;
}
