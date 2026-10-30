import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { OneOffAddMenu } from "./OneOffAddMenu";

vi.mock("../../templates/useLibrary", () => ({
  useLibrary: () => [{ kind: "nodes", name: "verification", definition: { steps: [{ id: "main", tasks: [{ id: "run" }] }] } }],
}));

type Ops = { op: string; [k: string]: unknown }[];
const mount = (send = vi.fn(async (_ops: Ops) => true)) => {
  const anchor = createRef<HTMLElement>();
  const onClose = vi.fn();
  render(<><button ref={anchor as never}>seam</button><OneOffAddMenu anchor={anchor} at={2} taken={["spec"]} send={send} onClose={onClose} /></>);
  return { send, onClose };
};

describe("OneOffAddMenu", () => {
  it("offers a library node and a custom agent node", () => {
    mount();
    expect(screen.getByRole("menuitem", { name: /From the library/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Custom agent node/ })).toBeInTheDocument();
  });

  it("a custom agent node sends the node, its task and its prompt", async () => {
    const { send, onClose } = mount();
    fireEvent.click(screen.getByRole("menuitem", { name: /Custom agent node/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Node id" }), { target: { value: "load_test" } });
    expect(screen.getByRole("textbox", { name: "Harness" })).toHaveValue("claude");
    fireEvent.change(screen.getByRole("textbox", { name: "Prompt" }), { target: { value: "run k6" } });
    fireEvent.click(screen.getByRole("button", { name: "Create & open →" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const ops = send.mock.calls[0][0];
    expect(ops[0]).toEqual({ op: "add_node", at: 2, id: "load_test", kind: "exec" });
    expect(ops).toContainEqual({ op: "add_task", container: "load_test", step: "run", id: "agent", kind: "agent" });
    expect(ops).toContainEqual({ op: "set_field", path: "load_test.run.agent", field: "prompt", value: "run k6" });
  });

  it("will not add a node with no prompt or a taken id, and ties the reason to the id field", () => {
    mount();
    fireEvent.click(screen.getByRole("menuitem", { name: /Custom agent node/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Node id" }), { target: { value: " spec " } });
    fireEvent.change(screen.getByRole("textbox", { name: "Prompt" }), { target: { value: "x" } });
    expect(screen.getByRole("button", { name: "Create & open →" })).toBeDisabled();
    const id = screen.getByRole("textbox", { name: /^Node id/ });
    expect(id).toHaveValue("spec");
    expect(id).toHaveAttribute("aria-invalid", "true");
    expect(id).toHaveAccessibleDescription(/spec is taken/);
  });

  it.each([["Escape", () => fireEvent.keyDown(document, { key: "Escape" })], ["an outside press", () => fireEvent.mouseDown(document.body)]] as const)(
    "keeps a typed prompt on %s; Cancel still closes it", (_, away) => {
      const { onClose } = mount();
      fireEvent.click(screen.getByRole("menuitem", { name: /Custom agent node/ }));
      fireEvent.change(screen.getByRole("textbox", { name: "Prompt" }), { target: { value: "run k6" } });
      away();
      expect(onClose).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(onClose).toHaveBeenCalled();
    });

  it("a library node sends add_node then extend", async () => {
    const { send } = mount();
    fireEvent.click(screen.getByRole("menuitem", { name: /From the library/ }));
    fireEvent.click(await screen.findByText("verification"));
    fireEvent.click(await screen.findByRole("button", { name: "Create & open →" }));
    await waitFor(() => expect(send).toHaveBeenCalled());
    expect(send.mock.calls[0][0]).toEqual([{ op: "add_node", at: 2, id: "verification", kind: "exec" }, { op: "extend", node: "verification", base: "verification" }]);
  });
});
