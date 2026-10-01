import { useEffect, useState } from "react";
import * as api from "../../../api";
import type { HarnessProviders, Harnesses } from "../../../types";

type Opts = { harnesses: Harnesses; providers: HarnessProviders } | "loading" | "failed";
let cache: Promise<{ harnesses: Harnesses; providers: HarnessProviders }> | null = null;

/** The agent task's choices (brief C.5): harnesses and agent profiles from
 *  `GET /harnesses/profiles`, efforts per provider from `/harnesses/providers`.
 *  Read once per page load. */
export function useHarnessOptions(): Opts {
  const [opts, setOpts] = useState<Opts>("loading");
  useEffect(() => {
    cache ??= Promise.all([api.getHarnesses(), api.getHarnessProviders()]).then(([harnesses, providers]) => ({ harnesses, providers }));
    let live = true;
    cache.then((o) => live && setOpts(o)).catch(() => {
      cache = null;
      if (live) setOpts("failed");
    });
    return () => {
      live = false;
    };
  }, []);
  return opts;
}

/** The effort values a harness's provider accepts; empty means any. */
export function effortsFor(o: Opts, harness: string): string[] {
  if (typeof o === "string") return [];
  const provider = o.harnesses.profiles.find((p) => p.id === harness)?.provider ?? harness;
  return o.providers.valid[provider]?.capabilities?.effort?.values ?? [];
}

/** For tests: forget the cached answer. */
export const resetHarnessOptions = () => void (cache = null);
