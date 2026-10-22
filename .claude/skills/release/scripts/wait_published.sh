#!/usr/bin/env bash
# Wait until each target serves VERSION, checking every 15s. Run it in the
# background: it ends once, with exit 0 when all are served.
# Usage: wait_published.sh VERSION [pypi] [brew] [openvsx]   (default: pypi)
# TRIES (default 120, 30 minutes) bounds each target's wait.
set -u
V="${1:?usage: wait_published.sh VERSION [pypi] [brew] [openvsx]}"; shift
[ $# -eq 0 ] && set -- pypi
served() {
  case "$1" in
    pypi) curl -sf -o /dev/null "https://pypi.org/pypi/kraft-sdlc/$V/json" ;;
    # The formula's url names the release asset, .../download/vX.Y.Z/...
    brew) curl -sf https://raw.githubusercontent.com/itsOmidKarami/homebrew-kraft/main/Formula/kraft.rb |
            grep '^  url' | grep -qF "/v$V/" ;;
    openvsx) [ "$(curl -sf https://open-vsx.org/api/kraft-sdlc/kraft |
                 python3 -c 'import sys,json; print(json.load(sys.stdin).get("version"))' 2>/dev/null)" = "$V" ] ;;
    *) echo "unknown target: $1"; exit 2 ;;
  esac
}
for t in "$@"; do
  for _ in $(seq 1 "${TRIES:-120}"); do
    served "$t" && { echo "$t ok"; continue 2; }
    sleep 15
  done
  echo "$t: still not serving $V; giving up"; exit 1
done
