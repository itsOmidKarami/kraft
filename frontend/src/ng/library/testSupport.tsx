import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import { Shell } from "../shell/Shell";
import { resetHarnessOptions } from "../templates/panes/useHarnessOptions";
import * as d from "../templates/draft/draftApi";
import type { Result } from "../templates/draft/types";
import { libView, PUBLISHED } from "./fixture";
import { LibraryPage } from "./LibraryPage";

/** The Library page's tests: the shell around it, the draft and the published library served from fixtures. Tests only. */
export const ok = <T,>(body: T) => Promise.resolve({ status: 200, body });

let at = "";
function Where() {
  at = useLocation().pathname;
  return null;
}
export const where = () => at;

export const mount = (path = "/templates/library") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route element={<Shell />}>
          <Route path="/templates/library" element={<LibraryPage />} />
          <Route path="/templates/library/:ref" element={<LibraryPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

export const draftWith = (extra: Partial<Result> = {}, draft = false) => vi.mocked(d.getDraft).mockImplementation(() => ok(libView(extra, draft)));

/** Call in `beforeEach`; pair with `vi.unstubAllGlobals()` in `afterEach`. */
export function setup() {
  resetHarnessOptions();
  vi.restoreAllMocks();
  vi.spyOn(api, "getHarnesses").mockResolvedValue({
    file: "", error: null,
    profiles: [{ id: "claude", provider: "claude", enabled: true, executable: null, defaults: {}, used_by: [], chains: [] }],
    agent_profiles: [{ id: "strong", effort: "high", model: {}, used_by: [], chains: [], problems: [] }],
  });
  vi.spyOn(api, "getHarnessProviders").mockResolvedValue({ valid: { claude: { id: "claude", kind: "cli", command: [], path: "", override: false, capabilities: { effort: { cli: [], values: ["low", "medium", "high"] } as never } } }, invalid: {} });
  localStorage.clear();
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  vi.spyOn(d, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(d, "getDraft").mockImplementation(() => ok(libView()));
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(PUBLISHED), { status: 200 })));
  useStore.setState({ workItems: {}, connection: "open" } as never);
}
