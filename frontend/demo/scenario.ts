import type { MockOptions } from "../e2e/contract/mocks/mockApi";

/** What the demo asks of the mocked API, so `apiShapes.test.ts` checks the answers the demo serves. `main.ts` adds
 *  the build's version, its event socket and `motion` (left out here: building it arms a timer). */
export const demoOptions = { ngBoard: true, areas: "default", harnesses: "floor" } as const satisfies MockOptions;
