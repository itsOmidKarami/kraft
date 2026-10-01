import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { WithHeader } from "./testkit";
import { YamlFrame } from "./YamlFrame";

const frame = (overview?: boolean) => render(
  <WithHeader>
    <YamlFrame pageKey="t" file="t.yaml" title="thing" icon="shield" yaml={"a: 1\nb: 2"} yamlNote="never shown" overview={overview ? <p>the overview</p> : undefined}>
      <h1>the page</h1>
    </YamlFrame>
  </WithHeader>,
);
const yamlButton = () => screen.getByRole("button", { name: "YAML" });

describe("YamlFrame", () => {
  it("has no pane until YAML is pressed, then shows the text read-only, and closes with the button", async () => {
    frame();
    expect(screen.queryByRole("complementary")).toBeNull();
    await userEvent.click(yamlButton());
    const pane = screen.getByRole("complementary", { name: "thing pane" });
    expect(within(pane).getByLabelText("t.yaml").textContent).toBe("a: 1\nb: 2");
    expect(within(pane).getByText("never shown")).toBeInTheDocument();
    expect(within(pane).getByText("t.yaml · saved on change")).toBeInTheDocument();
    expect(yamlButton()).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(yamlButton());
    expect(screen.queryByRole("complementary", { name: "thing pane" })).toBeNull();
  });

  it("closes from the pane's own collapse button", async () => {
    frame();
    await userEvent.click(yamlButton());
    await userEvent.click(screen.getByRole("button", { name: "Collapse pane" }));
    expect(screen.queryByRole("complementary", { name: "thing pane" })).toBeNull();
    expect(yamlButton()).toHaveAttribute("aria-pressed", "false");
  });

  it("with an overview the pane starts collapsed; YAML opens the YAML tab and a second press goes back to Overview", async () => {
    frame(true);
    expect(screen.getByRole("complementary", { name: "thing pane, collapsed" })).toBeInTheDocument();
    expect(screen.queryByText("the overview")).toBeNull();
    await userEvent.click(yamlButton());
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("t.yaml")).toBeInTheDocument();
    await userEvent.click(yamlButton());
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("the overview")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "thing pane" })).toBeInTheDocument();
  });
});
