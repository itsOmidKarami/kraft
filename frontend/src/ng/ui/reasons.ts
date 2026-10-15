/** The words for a disabled control, one source for the desktop and phone trees. */
export const ADD_TITLE = "Add a title to create it.";
export const NOTHING_TO_PUBLISH = "Nothing to publish: no draft.";
export const fixProblems = (n: number) => `Fix the ${n} ${n === 1 ? "problem" : "problems"} first.`;
export const CHAIN_IN_USE = (repos: string[]) => `Can't delete: ${repos.join(", ")} default to it.`;
export const WEBHOOK_OFF = (urlSet: boolean) => (urlSet ? "Turn on the webhook first." : "Set a webhook URL first.");
export const CHOOSE_REPO = "Choose a repo to create it in.";
export const COMMENT_NEEDS_TEXT = "A comment needs some text.";
export const ADD_TO_SUBMIT = "Add a comment or a note first.";
