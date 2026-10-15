import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { MovedAnchors } from "./MovedAnchors";

function Where() {
  const l = useLocation();
  return <p>{`at ${l.pathname}${l.hash}`}</p>;
}

describe("MovedAnchors", () => {
  it.each([
    ["#set-code", "at /settings/board-and-code#set-code"],
    ["#set-diff", "at /settings/board-and-code#set-diff"],
    ["#set-board", "at /settings/board-and-code#set-board"],
    ["#set-editor", "at /settings/board-and-code#set-editor"],
    ["#mode", "the look"],
    ["", "the look"],
  ])("Appearance%s lands on %s", (hash, shown) => {
    render(
      <MemoryRouter initialEntries={[`/settings/appearance${hash}`]}>
        <Routes>
          <Route path="/settings/appearance" element={<MovedAnchors><p>the look</p></MovedAnchors>} />
          <Route path="/settings/board-and-code" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText(shown)).toBeInTheDocument();
  });
});
