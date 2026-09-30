"""Writes `src/kraft/templates/lucide_icons.txt`: the kebab-case name of every
icon the installed `lucide-react` ships, sorted, one per line -- the names the
UI can draw, which `config_check` lints a template's `icon:` against.

Read from `frontend/node_modules`, so run `just setup` first and `just icons`
after every `lucide-react` bump; commit the result.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "frontend" / "node_modules" / "lucide-react" / "dist" / "esm" / "icons"
OUT = ROOT / "src" / "kraft" / "templates" / "lucide_icons.txt"


def main() -> None:
    names = sorted(p.name.removesuffix(".mjs") for p in ICONS.glob("*.mjs"))
    if not names:
        raise SystemExit(f"no icons under {ICONS}; run `just setup` first")
    OUT.write_text("".join(f"{n}\n" for n in names))
    print(f"{OUT}: {len(names)} icons")


if __name__ == "__main__":
    main()
