import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { PillInput } from "./PillInput";

function Harness({ start = ["git", "shell"], added = [] as string[], onChange = vi.fn() }) {
  const [values, setValues] = useState(start);
  return <PillInput label="allowed tools" values={values} added={added} empty="Empty allows no tools." onChange={(n) => { setValues(n); onChange(n); }} />;
}
const list = () => screen.getAllByRole("listitem").filter((li) => li.classList.contains("pill")).map((li) => li.textContent?.replace("×", ""));

describe("PillInput", () => {
  it("adds on Enter and on comma, trimmed, and ignores a duplicate or an empty one", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const box = screen.getByRole("textbox", { name: "Add to allowed tools" });
    await userEvent.type(box, "  editor {Enter}");
    await userEvent.type(box, "web,");
    await userEvent.type(box, "git{Enter}");
    await userEvent.type(box, "{Enter}");
    expect(list()).toEqual(["git", "shell", "editor", "web"]);
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it("removes the last pill on Backspace in an empty input, but not while there is text", async () => {
    render(<Harness />);
    const box = screen.getByRole("textbox", { name: "Add to allowed tools" });
    await userEvent.type(box, "x{Backspace}");
    expect(list()).toEqual(["git", "shell"]);
    await userEvent.keyboard("{Backspace}");
    expect(list()).toEqual(["git"]);
  });

  it("removes a pill with its × and with Delete or Backspace on the focused ×, handing focus back to the input", async () => {
    render(<Harness start={["git", "shell", "web"]} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove shell" }));
    expect(list()).toEqual(["git", "web"]);
    screen.getByRole("button", { name: "Remove git" }).focus();
    await userEvent.keyboard("{Delete}");
    expect(list()).toEqual(["web"]);
    expect(screen.getByRole("textbox", { name: "Add to allowed tools" })).toHaveFocus();
    screen.getByRole("button", { name: "Remove web" }).focus();
    await userEvent.keyboard("{Backspace}");
    expect(list()).toEqual([]);
  });

  it("marks the pills new in the draft, and says what an empty list means", async () => {
    render(<Harness start={["git", "web"]} added={["web"]} />);
    expect(screen.getByText("web").closest("li")).toHaveClass("is-added");
    expect(screen.getByText("git").closest("li")).not.toHaveClass("is-added");
    await userEvent.click(screen.getByRole("button", { name: "Remove git" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove web" }));
    expect(screen.getByText("Empty allows no tools.")).toBeInTheDocument();
  });

  it("announces what it did", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByRole("textbox", { name: "Add to allowed tools" }), "web{Enter}");
    expect(screen.getByRole("status")).toHaveTextContent("Added web");
    await userEvent.click(screen.getByRole("button", { name: "Remove web" }));
    expect(screen.getByRole("status")).toHaveTextContent("Removed web");
  });
});

describe("PillInput with a closed set", () => {
  const GRANTS = [{ value: "git-commit", summary: "A plain git commit" }, { value: "git-rebase" }, { value: "git-push" }];
  function Grants({ onChange = vi.fn() }) {
    const [values, setValues] = useState(["git-commit"]);
    return <PillInput label="escalation grants" values={values} choices={GRANTS} noun="grant" onChange={(n) => { setValues(n); onChange(n); }} />;
  }

  it("lists the values not yet added, and adds a pick", async () => {
    const onChange = vi.fn();
    render(<Grants onChange={onChange} />);
    const box = screen.getByRole("combobox", { name: "Add to escalation grants" });
    await userEvent.click(box);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["git-rebase", "git-push"]);
    await userEvent.type(box, "push");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenLastCalledWith(["git-commit", "git-push"]);
    expect(box).toHaveValue("");
  });

  it("refuses a value it does not list, keeping the text, and adds nothing", async () => {
    const onChange = vi.fn();
    render(<Grants onChange={onChange} />);
    const box = screen.getByRole("combobox", { name: "Add to escalation grants" });
    await userEvent.type(box, "sds{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("“sds” is not a grant. Pick one from the list.");
    expect(box).toHaveValue("sds");
    expect(onChange).not.toHaveBeenCalled();
  });
});
