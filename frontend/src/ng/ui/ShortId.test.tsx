import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShortId } from "./ShortId";
import { Toaster } from "./Toast";

const ID = "0123456789abcdef0123456789abcdef";
afterEach(() => vi.unstubAllGlobals());

describe("ng ShortId", () => {
  it("shows first8…last5 with the full id in its title, and a click copies it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(
      <>
        <ShortId id={ID} />
        <Toaster />
      </>,
    );
    const el = screen.getByRole("button", { name: `Copy id ${ID}` });
    expect(el).toHaveTextContent("01234567…bcdef");
    expect(el).toHaveAttribute("title", ID);
    await act(async () => void fireEvent.click(el));
    expect(writeText).toHaveBeenCalledWith(ID);
    expect(screen.getByRole("status")).toHaveTextContent("Copied id");
  });
});
