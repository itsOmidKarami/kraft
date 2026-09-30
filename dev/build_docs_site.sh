#!/usr/bin/env bash
# Build the published docs site into OUT_DIR: the latest stable release's text
# at the root (/kraft/) and main's at /kraft/next/. Both builds use this
# checkout's site code (theme, components, config), so a site fix reaches the
# release docs without a release; only docsite/content and .github/assets come
# from the tag. Needs docsite/node_modules (npm ci) and the repo's tags.
#
#   dev/build_docs_site.sh OUT_DIR
#   KRAFT_DOCS_STABLE_TAG=v1.0.8 dev/build_docs_site.sh OUT_DIR   # pick the tag
set -euo pipefail

OUT=${1:?usage: dev/build_docs_site.sh OUT_DIR}
ROOT=$(git rev-parse --show-toplevel)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

# The same rule as release.yml: exact vX.Y.Z, so rc/alpha/beta tags never count.
TAG=${KRAFT_DOCS_STABLE_TAG:-$(git -C "$ROOT" tag --list | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)}

generate() { # DOCSITE_DIR BASE CHANNEL DEST
  (cd "$1" && KRAFT_DOCS_BASE=$2 KRAFT_DOCS_CHANNEL=$3 KRAFT_DOCS_STABLE_VERSION=$TAG npx nuxt generate)
  mkdir -p "$4"
  # -L: docsite/public holds symlinks (icon.svg, assets), and the stable
  # build's point into a temp dir deleted on exit; copied as links they
  # dangle and the Pages upload's tar fails on them.
  cp -RL "$1/.output/public/." "$4/"
}

if [ -z "$TAG" ]; then
  echo "::warning::no stable vX.Y.Z tag; building main alone at /kraft/"
  generate "$ROOT/docsite" /kraft/ "" "$OUT"
  exit 0
fi

generate "$ROOT/docsite" /kraft/next/ next "$OUT/next"

# The stable build: a copy of this working tree's site code with the tag's
# content in place of main's. A copy, not a worktree of HEAD, so it builds the
# same (possibly uncommitted) site code as the next build above, and an
# interrupted run never leaves the tag's pages in docsite/. The copy keeps
# docsite/public's symlinks resolving (../../.github/assets,
# ../../frontend/public/icon.svg).
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/docsite" "$TMP/frontend/public"
(cd "$ROOT/docsite" && tar -cf - --exclude ./content --exclude ./node_modules \
  --exclude ./.nuxt --exclude ./.output --exclude ./.data --exclude ./dist-site .) | tar -xf - -C "$TMP/docsite"
cp "$ROOT/frontend/public/icon.svg" "$TMP/frontend/public/"
git -C "$ROOT" archive "$TAG" docsite/content .github/assets | tar -xf - -C "$TMP"
ln -s "$ROOT/docsite/node_modules" "$TMP/docsite/node_modules"
# The root 404.html comes from here, so GitHub Pages answers a missing path
# anywhere, /next/ included, with the release's 404 page.
generate "$TMP/docsite" /kraft/ stable "$OUT"
echo "built $TAG at $OUT and main at $OUT/next"
