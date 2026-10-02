import { useEffect, useState } from "react";
import * as api from "../../api";
import type { Repo } from "../../types";

let cache: Promise<Repo[]> | null = null;

/** The `repos.yaml` entry of the item's repo, for the models it gives each
 *  harness; null until read, or when the repo has none. Read once per page load. */
export function useRepoEntry(path: string): Repo | null {
  const [repo, setRepo] = useState<Repo | null>(null);
  useEffect(() => {
    cache ??= api.getRepos().then((r) => (Array.isArray(r?.repos) ? r.repos : []), () => []);
    let live = true;
    cache.then((list) => live && setRepo(list.find((r) => r.path === path) ?? null));
    return () => void (live = false);
  }, [path]);
  return repo;
}

/** For tests: forget the cached answer. */
export const resetRepoEntries = () => void (cache = null);
