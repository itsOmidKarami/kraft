import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mod, sendOnModEnter } from "./keys";

function Box({ send, ready = true }: { send: () => void; ready?: boolean }) {
  const [text, setText] = useState("");
  return <textarea aria-label="Note" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={sendOnModEnter(send, ready)} />;
}

describe("sendOnModEnter", () => {
  it("sends on ⌘↵ and on Ctrl+↵, and leaves a plain ↵ its newline", async () => {
    const send = vi.fn();
    render(<Box send={send} />);
    const box = screen.getByRole("textbox", { name: "Note" });
    await userEvent.type(box, "one{Enter}two");
    expect(box).toHaveValue("one\ntwo");
    expect(send).not.toHaveBeenCalled();
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    expect(send).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(send).toHaveBeenCalledTimes(2);
    expect(box).toHaveValue("one\ntwo");
  });

  it("does not send while the send button would refuse, nor on ⇧⌘↵ or ⌥⌘↵, nor mid-composition", () => {
    const send = vi.fn();
    const { unmount } = render(<Box send={send} ready={false} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter", metaKey: true });
    unmount();
    render(<Box send={send} />);
    const box = screen.getByRole("textbox");
    fireEvent.keyDown(box, { key: "Enter", metaKey: true, shiftKey: true });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true, altKey: true });
    fireEvent.keyDown(box, { key: "Enter", metaKey: true, isComposing: true });
    expect(send).not.toHaveBeenCalled();
  });
});

describe("mod", () => {
  const as = (platform: string, userAgent = "Mozilla/5.0") => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue(platform);
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);
  };
  afterEach(() => vi.restoreAllMocks());

  it("prints ⌘ on a Mac and an iPad, and Ctrl+ off one, as the handlers take either", () => {
    as("MacIntel");
    expect(mod("K")).toBe("⌘K");
    as("iPad");
    expect(mod("↵")).toBe("⌘↵");
    as("Linux x86_64");
    expect(mod("K")).toBe("Ctrl+K");
    as("Win32");
    expect(mod("Z")).toBe("Ctrl+Z");
    as("", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)");
    expect(mod("K")).toBe("⌘K");
  });
});
