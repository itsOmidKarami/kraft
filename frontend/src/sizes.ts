/** Bytes as `12.1 GB`: the largest unit the number reaches, one decimal at most (the server's `render.human_size` prints without the unit's B). */
export function humanSize(bytes: number): string {
  for (const [unit, size] of [["TB", 1024 ** 4], ["GB", 1024 ** 3], ["MB", 1024 ** 2]] as const) {
    if (bytes >= size) return `${(bytes / size).toFixed(1).replace(/\.0$/, "")} ${unit}`;
  }
  return `${Math.floor(bytes / 1024)} KB`;
}
