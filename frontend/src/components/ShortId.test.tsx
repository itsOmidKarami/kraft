import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { shortIds } from "../format";
import { Documents } from "../views/work_item/Inspector/Documents";
import { ShortId } from "./ShortId";
import { plainMarkdown } from "./Snippet";

const ID = "12a7f05a7934078cc17a96aee64cf059";

describe("text overflow policy (W5.11)", () => {
  it("ShortId shows first8…last5 and keeps the full id on hover", () => {
    render(<ShortId id={ID} />);
    const el = screen.getByTitle(ID);
    expect(el.textContent).toMatch(/^[0-9a-f]{8}…[0-9a-f]{5}$/);
    expect(el).toHaveClass("mono-id");
  });

  it("no bare 32-hex id survives in a title", () => {
    expect(shortIds(`Session ${ID}`)).toBe("Session 12a7f05a…cf059");
    expect(shortIds(ID)).not.toMatch(/[0-9a-f]{32}/);
  });

  it("a document path renders whole in a .path span, its title the full path", async () => {
    const path = ".engineering/reviews/2026-09-13-caching-layer.md";
    vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
      documents: [{ document_id: "d1", title: `Session ${ID}`, path, kind: "sessions" }],
    } as unknown as Awaited<ReturnType<typeof api.getWorkItemDocuments>>);
    render(
      <Documents
        workItemId="w1"
        eventCount={0}
        selected="d1"
        onSelect={() => {}}
        gatePending={false}
        gateArtifactPending={false}
      />,
    );
    const el = await screen.findByText(path);
    expect(el).toHaveClass("path");
    expect(el).toHaveAttribute("title", path);
    expect(screen.getByText("Session 12a7f05a…cf059")).toBeInTheDocument();
  });

  it("search snippets drop markdown syntax but keep snake_case", () => {
    expect(plainMarkdown("## Context\n**every** `findings_measured` [see](http://x)")).toBe(
      "Context\nevery findings_measured see",
    );
  });
});
