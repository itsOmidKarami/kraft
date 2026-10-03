/** When Kraft auto-escalates a stuck node that has no escalation task of its
 *  own, from the policy's `auto_escalate_delay_s`: "at once" for 0, never
 *  "after 0m", and seconds under a minute rather than rounding them to 0. */
export function autoEscalates(seconds: number): string {
  if (seconds <= 0) return "at once";
  if (seconds < 60) return `after ${seconds}s`;
  return `after ${Math.round(seconds / 60)}m`;
}
