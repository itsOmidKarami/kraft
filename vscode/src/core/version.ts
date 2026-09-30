function parse(v: string | undefined): [number, number] | null {
  const m = v?.match(/^(\d+)\.(\d+)\.\d+/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

// A source install: the unstamped 0.0.0, or a PEP 440 dev/local version, which
// only a checkout reports (setuptools-scm, e.g. `0.1.dev1+g3b1566c` from a
// shallow clone). A release is always a plain X.Y.Z.
const SOURCE = (v: string | undefined) => v === "0.0.0" || /\.dev\d|\+/.test(v ?? "");

export function compatible(extension: string, daemon: string | undefined): boolean {
  if (daemon === undefined) return false;
  if (SOURCE(extension) || SOURCE(daemon)) return true;
  const e = parse(extension);
  const d = parse(daemon);
  if (!e || !d) return false;
  return e[0] === d[0] && d[1] >= e[1];
}
