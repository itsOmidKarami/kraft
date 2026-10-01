import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ScreenHeader } from "./ScreenHeader";
import { resetTrail, useTrail } from "./trail";

const idx = () => (window.history.state as { idx: number }).idx;

function Where() {
  const { pathname, search } = useLocation();
  return <output aria-label="where">{pathname + search}</output>;
}
function Screen({ name }: { name: string }) {
  const go = useNavigate();
  return (
    <>
      {name !== "board" && <ScreenHeader id={name} />}
      <button type="button" onClick={() => go("/work-items/a")}>open item</button>
      <button type="button" onClick={() => go("/work-items/a/nodes/n")}>open node</button>
      <button type="button" onClick={() => go("/work-items/a/nodes/n?sel=n.s.t")}>open task</button>
    </>
  );
}
function Harness() {
  useTrail();
  return (
    <>
      <Where />
      <Routes>
        <Route path="/" element={<Screen name="board" />} />
        <Route path="/work-items/:id" element={<Screen name="item" />} />
        <Route path="/work-items/:id/nodes/:node" element={<Screen name="node" />} />
      </Routes>
    </>
  );
}
const where = () => screen.getByLabelText("where").textContent;

beforeEach(() => resetTrail());
afterEach(() => window.history.replaceState(null, "", "/"));

describe("Back (A.3)", () => {
  it("pops when the entry before it is the parent", async () => {
    window.history.replaceState(null, "", "/");
    render(<BrowserRouter><Harness /></BrowserRouter>);
    await userEvent.click(screen.getByRole("button", { name: "open item" }));
    expect(where()).toBe("/work-items/a");
    expect(idx()).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: /Board/ }));
    await waitFor(() => expect(where()).toBe("/"));
    expect(idx()).toBe(0);
  });

  it("replaces with the parent on a deep link, so Back never loops or leaves the app", async () => {
    window.history.replaceState(null, "", "/work-items/a/nodes/n?sel=n.s.t");
    render(<BrowserRouter><Harness /></BrowserRouter>);
    for (const next of ["/work-items/a/nodes/n", "/work-items/a", "/"]) {
      await userEvent.click(screen.getByRole("button", { name: /^Back|Node|Chain|Board/ }));
      await waitFor(() => expect(where()).toBe(next));
      expect(idx()).toBe(0);
    }
    expect(screen.queryByRole("button", { name: /Board|Back/ })).toBeNull();
  });

  it("labels the control with the parent", async () => {
    window.history.replaceState(null, "", "/work-items/a/nodes/n?sel=n.s.t");
    render(<BrowserRouter><Harness /></BrowserRouter>);
    expect(screen.getByRole("button", { name: "Node" })).toBeInTheDocument();
  });
});
