import type { Api } from "./api";

export async function approveGate(api: Api, id: string, gate: string): Promise<void> {
  const artifact = await api.getArtifact(id);
  await api.approve(id, gate, artifact?.digest);
}
