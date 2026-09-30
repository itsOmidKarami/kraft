// Two UIs, one build (UX V2 spec §2.2): static imports run before any code,
// so only a dynamic import keeps one UI's CSS off the other's page.
if (location.pathname === "/ng" || location.pathname.startsWith("/ng/")) void import("./ng/boot");
else void import("./boot");
