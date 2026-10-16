#!/usr/bin/env bash
# Build the published docs site into OUT_DIR: the latest stable release's text
# at the root (/), each older major's last release frozen at /vN/, and main's
# at /next/ (dev/docs_versions.py picks the tags). Every build uses this
# checkout's site code (theme, components, config), so a site fix reaches the
# release docs without a release; only docsite/content and .github/assets (and
# an older release's diagram images) come from the tag. Needs
# docsite/node_modules (npm ci) and the repo's tags.
#
#   dev/build_docs_site.sh OUT_DIR
#   KRAFT_DOCS_STABLE_TAG=v1.0.8 dev/build_docs_site.sh OUT_DIR   # pick the latest's tag
#
# Escape hatch, off by default: a frozen major that no longer builds with this
# checkout's components can be built with its own tag's site code instead
# (its own `npm ci` of the tag's lockfile). Name it by its id (v1):
#
#   KRAFT_DOCS_OWN_SITE="v1" dev/build_docs_site.sh OUT_DIR
#
# That build has the old look and no version menu, the origin baked into the
# tag's nuxt.config.ts (so its canonical, og:url and sitemap URLs name that
# origin, not kraft-sdlc.dev), and a noindex tag added here after the build.
# CONTRIBUTING.md, "Versions of the docs", has the rest.
#
# The dashboard demo (frontend/demo/) goes beside them at /demo/, built from
# this checkout: one copy, not one per version. Needs frontend/node_modules too.
set -euo pipefail

OUT=${1:?usage: dev/build_docs_site.sh OUT_DIR}
ROOT=$(git rev-parse --show-toplevel)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

PLAN=$(python3 "$ROOT/dev/docs_versions.py")
# Every build reads the whole list, for its version menu.
export KRAFT_DOCS_VERSIONS=$(python3 "$ROOT/dev/docs_versions.py" --json)

generate() { # DOCSITE_DIR ID BASE DEST
  (cd "$1" && KRAFT_DOCS_VERSION=$2 KRAFT_DOCS_BASE=$3 npx nuxt generate </dev/null)
  mkdir -p "$4"
  # -L: docsite/public holds symlinks (icon.svg, assets), and a tag build's
  # point into a temp dir deleted on exit; copied as links they dangle and the
  # Pages upload's tar fails on them.
  cp -RL "$1/.output/public/." "$4/"
}

# The web UI on its mocked API, as static files (frontend/demo/main.ts).
build_demo() { # DEST
  (cd "$ROOT/frontend" && KRAFT_DEMO_BASE=/demo/ npx vite build --mode demo --outDir "$1" --emptyOutDir </dev/null)
}

if [ -z "$PLAN" ]; then
  echo "::warning::no stable vX.Y.Z tag; building main alone at /"
  generate "$ROOT/docsite" "" / "$OUT"
  build_demo "$OUT/demo"
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# A tag's build is a copy of this working tree's site code with the tag's
# content in place of main's. A copy, not a worktree of HEAD, so it builds the
# same (possibly uncommitted) site code as the main build, and an interrupted
# run never leaves the tag's pages in docsite/. The copy keeps docsite/public's
# symlinks resolving (../../.github/assets, ../../frontend/public/icon.svg).
build_tag() { # ID BASE TAG DEST
  local w="$TMP/$1"
  mkdir -p "$w/docsite" "$w/frontend/public"
  (cd "$ROOT/docsite" && tar -cf - --exclude ./content --exclude ./node_modules \
    --exclude ./.nuxt --exclude ./.output --exclude ./.data --exclude ./dist-site .) | tar -xf - -C "$w/docsite"
  cp "$ROOT/frontend/public/icon.svg" "$w/frontend/public/"
  git -C "$ROOT" archive "$3" docsite/content .github/assets | tar -xf - -C "$w"
  # The tag was cut before its release stamped the "Added in next" notes (the
  # stamp pull request comes after), so stamp this copy the way that pull
  # request does, with this copy's own tag.
  python3 "$ROOT/dev/stamp_docs_added_in.py" "${3#v}" "$w/docsite/content" >/dev/null ||
    echo "::warning::$3's docs still carry _Added in next_ notes: a feature shipped under a patch label"
  # A release up to v2.0.0 shows its diagrams as images from public/diagrams/,
  # which main no longer has (its diagrams are markup: the Diagram*.vue
  # components), so they come from the tag with its pages.
  if git -C "$ROOT" cat-file -e "$3:docsite/public/diagrams" 2>/dev/null; then
    git -C "$ROOT" archive "$3" docsite/public/diagrams | tar -xf - -C "$w"
  fi
  # A frozen major keeps the words its own components held: StageStrip draws
  # the default chain's stage names, which a later release renames ("draft MR"
  # became "draft PR"). A component that holds text a page shows goes in this
  # list; the tag's copy replaces main's if the tag has one.
  if [ "$1" != latest ]; then
    for component in StageStrip; do
      path=docsite/app/components/$component.vue
      if git -C "$ROOT" cat-file -e "$3:$path" 2>/dev/null; then
        git -C "$ROOT" show "$3:$path" >"$w/$path"
      fi
    done
  fi
  ln -s "$ROOT/docsite/node_modules" "$w/docsite/node_modules"
  generate "$w/docsite" "$1" "$2" "$4"
}

# The escape hatch: the tag's own site code and lockfile. Its nuxt.config.ts
# takes the base from KRAFT_DOCS_BASE and knows nothing of the version list,
# so it draws no version menu.
build_own_site() { # ID BASE TAG DEST
  local w="$TMP/$1"
  mkdir -p "$w"
  git -C "$ROOT" archive "$3" docsite .github/assets frontend/public/icon.svg | tar -xf - -C "$w"
  (cd "$w/docsite" && npm ci --no-audit --no-fund </dev/null)
  generate "$w/docsite" "" "$2" "$4"
  # The old config marks only main's build noindex: do it here, replacing the
  # page's robots tag (two of them, and the stricter wins, is not a state to ship).
  find "$4" -name '*.html' -exec perl -0pi -e 'unless (s#<meta name="robots"[^>]*>#<meta name="robots" content="noindex, nofollow">#) { s#</head>#<meta name="robots" content="noindex, nofollow"></head># }' {} +
}

# PLAN is in build order: main, the older majors, the latest last, so its
# 404.html and robots.txt are the ones at the root, which is where GitHub Pages
# looks for them for a missing path in any version.
while read -r id base tag <&3; do
  dest=$OUT${base%/}
  started=$SECONDS
  if [ "$id" = next ]; then
    generate "$ROOT/docsite" "$id" "$base" "$dest"
  elif [[ " ${KRAFT_DOCS_OWN_SITE:-} " == *" $id "* ]]; then
    build_own_site "$id" "$base" "$tag" "$dest"
  else
    build_tag "$id" "$base" "$tag" "$dest"
  fi
  echo "built $id ($tag) at $dest in $((SECONDS - started))s"
done 3<<<"$PLAN"

started=$SECONDS
build_demo "$OUT/demo"
echo "built the dashboard demo at $OUT/demo in $((SECONDS - started))s"
