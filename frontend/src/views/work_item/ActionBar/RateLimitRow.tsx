import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Clock } from "@phosphor-icons/react";
import type { WorkItem } from "../../../types";

function mmss(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** 06's "the rate-limit countdown row" (screen 22) — a second row inside
 *  the bar, `rate_limited` only. The `🕒 Anthropic API · 429 · Retry-After
 *  600s` literal from the screen note has no backing field
 *  (`rate_limit_hit`'s payload carries `rate_limit_type`/`resets_at`, not a
 *  provider name or HTTP status) — omitted per common rules ("omit any part
 *  the API doesn't expose"). */
export function RateLimitRow({ item }: { item: WorkItem }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!item.retry_at) return null;
  const remainingMs = Date.parse(item.retry_at) - now;
  const rl = item.rate_limit;
  return (
    <div className="rate-limit-row" data-testid="rate-limit-row">
      <Clock size={13} />
      <span>next relaunch in {mmss(remainingMs)}</span>
      {rl && (
        <span>
          · {rl.count} of {rl.cap} relaunches used
        </span>
      )}
      <Link to="/settings/policy">Policy → Rate limits</Link>
    </div>
  );
}
