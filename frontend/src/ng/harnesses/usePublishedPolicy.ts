import { useEffect, useState } from "react";
import { request } from "../http";

export interface Published {
  tools: string[] | null;
  grants: string[] | null;
}

const list = (v: unknown): string[] | null => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : null);

/** The published `policy.yaml`'s allowed tools and escalation grants (`GET /policy` reads the file),
 *  so a pill that is new in the draft can be drawn green. Read again when the draft opens or closes. */
export function usePublishedPolicy(draftOpen: boolean): Published | null {
  const [p, setP] = useState<Published | null>(null);
  useEffect(() => {
    let live = true;
    request<{ maxima?: { allowed_tools?: unknown }; defaults?: { escalation_grants?: unknown } }>("/policy").then((a) => {
      if (live && a.status === 200) setP({ tools: list(a.body.maxima?.allowed_tools), grants: list(a.body.defaults?.escalation_grants) });
    });
    return () => void (live = false);
  }, [draftOpen]);
  return p;
}
