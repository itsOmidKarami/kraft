/** The rate-limit poller's steer for the relaunch (`rate_limit_retry.RESUME_PROMPT`). On its own, so the sample data
 *  (ngItems.ts) and the doors (transitions.ts) both read it with no import cycle between them. */
export const RESUME_PROMPT = "You were working on this task when the agent hit an API rate limit and stopped. That limit has now reset — continue the work from where you left off.";
