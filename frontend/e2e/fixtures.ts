// Extension point for shared Playwright fixtures. Nothing custom is needed yet,
// so this just re-exports the base test/expect — specs import from here so any
// future fixture wiring lands in one place.
export { expect, test } from "@playwright/test";
