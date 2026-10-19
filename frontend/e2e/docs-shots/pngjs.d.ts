// The one call shots.ts makes into pngjs, which ships no types.
declare module "pngjs" {
  export const PNG: { sync: { read(data: Buffer): { width: number; height: number; data: Buffer } } };
}
