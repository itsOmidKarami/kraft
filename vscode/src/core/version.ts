function parse(v: string | undefined): [number, number] | null {
  const m = v?.match(/^(\d+)\.(\d+)\.\d+/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

const SOURCE = (v: string | undefined) => v === "0.0.0" || v?.startsWith("0.0.0+") === true;

export function compatible(extension: string, daemon: string | undefined): boolean {
  if (daemon === undefined) return false;
  if (SOURCE(extension) || SOURCE(daemon)) return true;
  const e = parse(extension);
  const d = parse(daemon);
  if (!e || !d) return false;
  return e[0] === d[0] && d[1] >= e[1];
}
