import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { useStore } from "./store";
import { App } from "./App";

beforeEach(() => {
  useStore.setState({ workItems: {}, connection: "reconnecting" } as never);
  vi.spyOn(api, "getHealth").mockResolvedValue({
    status: "degraded",
    invalid_templates: { broken: "broken.yaml: bad hook" },
    invalid_policy: [],
  });
});

describe("App", () => {
  it("shows the reconnecting badge and a degraded-health flag", async () => {
    render(<App />);
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
    expect(await screen.findByText(/broken/)).toBeInTheDocument();
  });
});
