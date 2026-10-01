import { useEffect, useState } from "react";
import { request } from "../http";

/** One provider as `GET /harnesses` reports it (the status list). */
export interface ProviderStatus {
  id: string;
  executable: string;
  executable_found: boolean;
  efforts: string[];
  models: string[];
  capabilities: Record<string, { cli: string[]; values: string[] }>;
}

let cache: Promise<ProviderStatus[]> | null = null;

/** The providers' accepted efforts and models and their capabilities, read once per page load. */
export function useProviders(): ProviderStatus[] {
  const [list, setList] = useState<ProviderStatus[]>([]);
  useEffect(() => {
    cache ??= request<ProviderStatus[]>("/harnesses").then((a) => (a.status === 200 && Array.isArray(a.body) ? a.body : []));
    let live = true;
    cache.then((l) => live && setList(l));
    return () => void (live = false);
  }, []);
  return list;
}

/** For tests: forget the cached answer. */
export const resetProviders = () => void (cache = null);
