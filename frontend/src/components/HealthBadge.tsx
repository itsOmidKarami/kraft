import { useEffect, useState } from "react";
import * as api from "../api";
import type { Health } from "../types";

export function HealthBadge() {
  const [h, setH] = useState<Health | null>(null);
  useEffect(() => {
    const load = () => api.getHealth().then(setH).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  if (!h || h.status !== "degraded") return null;
  const text = `degraded: ${[...Object.values(h.invalid_templates), ...h.invalid_policy].join(", ")}`;
  return (
    <span className="health-badge" title={text}>
      {text}
    </span>
  );
}
