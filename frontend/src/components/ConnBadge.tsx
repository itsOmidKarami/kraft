import { useStore } from "../store";

export function ConnBadge() {
  const c = useStore((s) => s.connection);
  if (c === "open") return null;
  return <span className="conn-badge">{c === "connecting" ? "connecting…" : "reconnecting…"}</span>;
}
