import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { LibraryComponent } from "../../types";
import { DraftLibrary, resetLibrary, useLibrary } from "./useLibrary";

const comp = (id: string): LibraryComponent => ({ id, kind: id.split(".")[0], name: id.split(".")[1], definition: {}, used_by: [], issues: [] });

beforeEach(() => {
  resetLibrary();
  vi.restoreAllMocks();
  vi.spyOn(api, "getLibrary").mockResolvedValue({ file: "", text: "", components: [comp("tasks.published")] });
});

describe("useLibrary", () => {
  it("lists the published library when no page provides a draft's", async () => {
    const { result } = renderHook(() => useLibrary());
    expect(result.current).toBe("loading");
    await waitFor(() => expect(result.current).toEqual([comp("tasks.published")]));
  });

  it("lists what the Library page's draft holds in its place, so an added component is offered before it is published", async () => {
    const draft = [comp("tasks.added")];
    const wrapper = ({ children }: { children: ReactNode }) => <DraftLibrary.Provider value={draft}>{children}</DraftLibrary.Provider>;
    const { result } = renderHook(() => useLibrary(), { wrapper });
    expect(result.current).toEqual(draft);
  });
});
