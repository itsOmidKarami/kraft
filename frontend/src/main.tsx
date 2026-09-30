import { applyTheme, savedTheme } from "./theme";

// Two UIs, one build (UX V2 spec §2.2): static imports run before any code,
// so only a dynamic import keeps one UI's CSS off the other's page.
if (location.pathname === "/ng" || location.pathname.startsWith("/ng/")) void import("./ng/boot");
else {
  // The shipped UI's saved theme, before its CSS can paint (W1.3). boot.tsx
  // applies it too, but only once the dynamic import resolves, which can be
  // after the stylesheet lands: the page painted Nocturne dark first.
  // theme.ts imports no CSS, so this keeps the shipped styles off /ng.
  const saved = savedTheme();
  if (saved) applyTheme(saved.palette, saved.mode);
  void import("./boot");
}
