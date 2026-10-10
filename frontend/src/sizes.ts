/** Bytes as `12.1G`: the largest unit the number reaches, one decimal at most (the server's `render.human_size`). */
export function humanSize(bytes: number): string {
  for (const [unit, size] of [["T", 1024 ** 4], ["G", 1024 ** 3], ["M", 1024 ** 2]] as const) {
    if (bytes >= size) return `${(bytes / size).toFixed(1).replace(/\.0$/, "")}${unit}`;
  }
  return `${Math.floor(bytes / 1024)}K`;
}
