import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Combobox, unlisted, type Choice } from "./Combobox";

const ACTIONS: Choice[] = [
  { value: "kraft.verify_changed_test_scopes", summary: "Run the repo's test scopes the change touches" },
  { value: "kraft.mr_rebase", summary: "Rebase the worktree onto the item's base branch" },
];
const INPUTS: Choice[] = [{ value: "review_package" }, { value: "carried_findings" }, { value: "previous_review" }];

/** A controlled field, as every caller holds one. */
function Field({ start = "", onPick, onKeyDown, ...rest }: Partial<React.ComponentProps<typeof Combobox>> & { start?: string }) {
  const [text, setText] = useState(start);
  return <Combobox aria-label="action" noun="action" listLabel="Actions" choices={ACTIONS} {...rest} value={text} onChange={setText} onPick={onPick} onKeyDown={onKeyDown} />;
}

const field = () => screen.getByRole("combobox", { name: "action" });
const shown = () => within(screen.getByRole("listbox", { hidden: true })).queryAllByRole("option").map((o) => o.querySelector(".cbx-value")!.textContent);

describe("Combobox", () => {
  it("shows every value on focus with an empty field, each with its summary, and closes on blur", async () => {
    render(<Field />);
    expect(field()).toHaveAttribute("aria-expanded", "false");
    await userEvent.tab();
    expect(field()).toHaveFocus();
    expect(field()).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByRole("listbox", { name: "Actions" });
    expect(field()).toHaveAttribute("aria-controls", list.id);
    expect(shown()).toEqual(["kraft.verify_changed_test_scopes", "kraft.mr_rebase"]);
    expect(within(list).getByText("Rebase the worktree onto the item's base branch")).toBeInTheDocument();
    await userEvent.tab();
    expect(field()).toHaveAttribute("aria-expanded", "false");
  });

  it("filters by what is typed, in the value or its summary, and shows the whole list again on a picked value", async () => {
    render(<Field />);
    await userEvent.type(field(), "rebase");
    expect(shown()).toEqual(["kraft.mr_rebase"]);
    await userEvent.clear(field());
    await userEvent.type(field(), "SCOPES");
    expect(shown()).toEqual(["kraft.verify_changed_test_scopes"]);
    await userEvent.clear(field());
    await userEvent.type(field(), "worktree");
    expect(shown()).toEqual(["kraft.mr_rebase"]);
    await userEvent.clear(field());
    await userEvent.type(field(), "kraft.mr_rebase");
    expect(shown()).toEqual(["kraft.verify_changed_test_scopes", "kraft.mr_rebase"]);
  });

  it("moves with ↓ and ↑ (wrapping), picks with Enter, and only then calls onPick", async () => {
    const onPick = vi.fn();
    const onKeyDown = vi.fn();
    render(<Field onPick={onPick} onKeyDown={onKeyDown} />);
    await userEvent.click(field());
    await userEvent.keyboard("{ArrowDown}");
    const first = screen.getAllByRole("option")[0];
    expect(field()).toHaveAttribute("aria-activedescendant", first.id);
    expect(first).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(field()).toHaveAttribute("aria-activedescendant", first.id);
    await userEvent.keyboard("{ArrowUp}");
    expect(field()).toHaveAttribute("aria-activedescendant", screen.getAllByRole("option")[1].id);
    expect(onPick).not.toHaveBeenCalled();
    await userEvent.keyboard("{Enter}");
    expect(field()).toHaveValue("kraft.mr_rebase");
    expect(onPick).toHaveBeenCalledWith("kraft.mr_rebase");
    expect(field()).toHaveAttribute("aria-expanded", "false");
    expect(onKeyDown).not.toHaveBeenCalledWith(expect.objectContaining({ key: "Enter" }));
    // With the list closed, Enter is the field's own (its save).
    await userEvent.keyboard("{Enter}");
    expect(onKeyDown).toHaveBeenCalledWith(expect.objectContaining({ key: "Enter" }));
  });

  it("picks a value with a click, keeping the focus in the field", async () => {
    const onPick = vi.fn();
    render(<Field onPick={onPick} />);
    await userEvent.click(field());
    await userEvent.click(screen.getByText("kraft.verify_changed_test_scopes"));
    expect(onPick).toHaveBeenCalledWith("kraft.verify_changed_test_scopes");
    expect(field()).toHaveFocus();
  });

  it("closes the list on Escape without the field seeing it; the next Escape is the field's", async () => {
    const onKeyDown = vi.fn();
    const outer = vi.fn();
    render(<div onKeyDown={outer}><Field onKeyDown={onKeyDown} /></div>);
    await userEvent.click(field());
    await userEvent.keyboard("{Escape}");
    expect(field()).toHaveAttribute("aria-expanded", "false");
    expect(onKeyDown).not.toHaveBeenCalled();
    expect(outer).not.toHaveBeenCalled();
    await userEvent.keyboard("{Escape}");
    expect(onKeyDown).toHaveBeenCalledWith(expect.objectContaining({ key: "Escape" }));
  });

  it("flags a closed set's value that matches nothing where it is typed, and a partial one once it is left", async () => {
    render(<><Field closed /><button type="button">elsewhere</button></>);
    await userEvent.type(field(), "kraft.m");
    expect(field()).not.toHaveAttribute("aria-invalid");
    await userEvent.type(field(), "x");
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("status")).toHaveTextContent("No action matches “kraft.mx”.");
    await userEvent.clear(field());
    await userEvent.type(field(), "kraft.m");
    await userEvent.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(field()).toHaveAttribute("aria-invalid", "true");
  });

  it("takes any text in an open set: the list only suggests", async () => {
    render(<><Field /><button type="button">elsewhere</button></>);
    await userEvent.type(field(), "sds");
    expect(field()).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByRole("status")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(field()).not.toHaveAttribute("aria-invalid");
  });

  it("completes a list's last entry, leaving out the ones it holds, and flags an unlisted earlier one", async () => {
    render(<Field choices={INPUTS} multiple closed />);
    await userEvent.type(field(), "review_package, ");
    expect(shown()).toEqual(["carried_findings", "previous_review"]);
    await userEvent.type(field(), "prev");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(field()).toHaveValue("review_package, previous_review");
    await userEvent.clear(field());
    await userEvent.type(field(), "sds, carr");
    expect(shown()).toEqual(["carried_findings"]);
    expect(field()).toHaveAttribute("aria-invalid", "true");
  });

  it("unlisted names what a text holds that is not a choice", () => {
    expect(unlisted("sds", ACTIONS)).toEqual(["sds"]);
    expect(unlisted(" kraft.mr_rebase ", ACTIONS)).toEqual([]);
    expect(unlisted("", ACTIONS)).toEqual([]);
    expect(unlisted("review_package, sds,", INPUTS, true)).toEqual(["sds"]);
  });
});
