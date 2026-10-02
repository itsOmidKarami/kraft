import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Banner, QuestionCard } from "./Banner";
import { detail, stubFetch } from "./testkit";

afterEach(() => vi.unstubAllGlobals());
const stop = (kind: string, over = {}) => ({ kind, node: "verification", task: null, resume_at: null, reason: null, ...over }) as never;

describe("Banner", () => {
  it("shows a gate stop with Open gate, which opens that gate", async () => {
    const onOpenGate = vi.fn();
    render(<Banner item={detail({ display_status: "needs_you", stop: stop("gate", { node: "final_review" }), pending_gate: "final_review" })} onOpenGate={onOpenGate} onRaise={() => {}} reload={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for your approval at final_review.");
    await userEvent.click(screen.getByRole("button", { name: "Open gate" }));
    expect(onOpenGate).toHaveBeenCalledWith("final_review");
  });

  it("shows a budget stop with Raise cap, which opens the budget editor in Config", async () => {
    const onRaise = vi.fn();
    render(<Banner item={detail({ display_status: "needs_you", stop: stop("budget", { reason: "Running time hit its 8h cap", scope: "work_item" }) })} onOpenGate={() => {}} onRaise={onRaise} reload={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Running time hit its 8h cap at verification.");
    await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
    expect(onRaise).toHaveBeenCalled();
  });

  it("offers no raise for a budget stop the item cannot raise, and says where it is raised", () => {
    // The daily cap, though the item is at its own $5 too: /budget/raise would answer 409.
    render(<Banner item={detail({ display_status: "needs_you", stop: stop("budget", { reason: "budget cap reached: $50.00 spent on today, across every work item, cap $50.00.", scope: "daily" }), budget_cap: { cap_usd: 5, source: "item", spent_usd: 5 } })} onOpenGate={() => {}} onRaise={() => {}} reload={() => {}} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("The item can't raise this cap: the policy or the chain sets it.");
  });

  it("opens Config, labelled so, for a cap stop that names no limit to raise", async () => {
    const onRaise = vi.fn();
    render(<Banner item={detail({ display_status: "needs_you", stop: stop("cap", { reason: "gate review waited past its timeout" }) })} onOpenGate={() => {}} onRaise={onRaise} reload={() => {}} />);
    expect(screen.queryByRole("button", { name: "Raise cap" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Open config" }));
    expect(onRaise).toHaveBeenCalled();
  });

  describe("raising the limit a cap stop names", () => {
    const capped = (limit: object) => detail({ display_status: "needs_you", stop: stop("cap", { reason: "verify.fix_loop exhausted after 3 fix cycle(s)", limit }) });
    const mount = (limit: object, answers = {}) => {
      const calls = stubFetch(answers);
      const reload = vi.fn();
      const onRaise = vi.fn();
      render(<Banner item={capped(limit)} onOpenGate={() => {}} onRaise={onRaise} reload={reload} />);
      return { calls, reload, onRaise };
    };
    const writes = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

    it.each([
      [{ path: "verification", key: "max_attempts", value: 3, maximum: 5 }, "4", { policy: { paths: { verification: { max_attempts: 4 } } } }],
      [{ path: "verification", key: "timeout_minutes", value: 30, maximum: null }, "45", { policy: { paths: { verification: { timeout_minutes: 45 } } } }],
      [{ path: "", key: "time_cap_minutes", value: 60, maximum: 120 }, "90", { policy: { time_cap_minutes: 90 } }],
      [{ path: "", key: "total_time_cap_minutes", value: 60, maximum: null }, "480", { policy: { total_time_cap_minutes: 480 } }],
    ])("%j: Save & retry patches the item's policy, then retries, then reloads", async (limit, typed, body) => {
      const { calls, reload, onRaise } = mount(limit);
      await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
      expect(onRaise).not.toHaveBeenCalled();
      const input = screen.getByRole("spinbutton");
      await userEvent.clear(input);
      await userEvent.type(input, typed);
      await userEvent.click(screen.getByRole("button", { name: "Save & retry" }));
      await waitFor(() => expect(reload).toHaveBeenCalled());
      expect(writes(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body }, { method: "POST", path: "/work-items/w1/retry", body: {} }]);
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("keeps the item's other policy overrides: a PATCH policy replaces the whole override", async () => {
      const calls = stubFetch({});
      const policy_override = { budget_usd: 5, max_attempts: 4, paths: { verification: { timeout_minutes: 30 }, review: { max_attempts: 2 } } };
      render(<Banner item={{ ...capped({ path: "verification", key: "max_attempts", value: 3, maximum: 5 }), policy_override }} onOpenGate={() => {}} onRaise={() => {}} reload={() => {}} />);
      await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
      await userEvent.clear(screen.getByRole("spinbutton"));
      await userEvent.type(screen.getByRole("spinbutton"), "5");
      await userEvent.click(screen.getByRole("button", { name: "Save & retry" }));
      await waitFor(() => expect(writes(calls)).toHaveLength(2));
      expect(writes(calls)[0]).toMatchObject({ body: { policy: { budget_usd: 5, max_attempts: 4, paths: { verification: { timeout_minutes: 30, max_attempts: 5 }, review: { max_attempts: 2 } } } } });
    });

    it("shows the current value and the maximum, and saves only a value above the current one", async () => {
      mount({ path: "verification", key: "max_attempts", value: 3, maximum: 5 });
      await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
      expect(screen.getByRole("dialog", { name: "Raise fix attempts on verification" })).toHaveTextContent("Now 3. Maximum 5.");
      expect(screen.getByRole("button", { name: "Save & retry" })).toBeDisabled();
      await userEvent.type(screen.getByRole("spinbutton"), "{backspace}2");
      expect(screen.getByRole("button", { name: "Save & retry" })).toBeDisabled();
      await userEvent.type(screen.getByRole("spinbutton"), "{backspace}4");
      expect(screen.getByRole("button", { name: "Save & retry" })).toBeEnabled();
    });

    it("shows a refusal inline and does not retry", async () => {
      const { calls, reload } = mount({ path: "verification", key: "max_attempts", value: 3, maximum: 5 }, { "PATCH /work-items/w1": [422, { detail: "policy.paths.verification.max_attempts: 6 exceeds the maximum 5" }] });
      await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
      await userEvent.type(screen.getByRole("spinbutton"), "{backspace}6");
      await userEvent.click(screen.getByRole("button", { name: "Save & retry" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("6 exceeds the maximum 5");
      expect(writes(calls).map((c) => c.method)).toEqual(["PATCH"]);
      expect(reload).not.toHaveBeenCalled();
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("says the limit was raised when only the retry is refused", async () => {
      const { reload } = mount({ path: "", key: "time_cap_minutes", value: 60, maximum: null }, { "POST /work-items/w1/retry": [409, { detail: "all 2 slots are busy" }] });
      await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
      await userEvent.type(screen.getByRole("spinbutton"), "{backspace}{backspace}90");
      await userEvent.click(screen.getByRole("button", { name: "Save & retry" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Raised to 90, but the retry failed: all 2 slots are busy");
      expect(reload).not.toHaveBeenCalled();
    });
  });

  describe("a policy budget_usd stop (Kraft-9d8b2.59)", () => {
    const budgeted = (limit: object) => detail({ display_status: "needs_you", stop: stop("budget", { reason: "budget_usd reached: $5.00 spent in the work item, cap $5.00", limit }) });
    const mount = (limit: object, answers = {}) => {
      const calls = stubFetch(answers);
      const reload = vi.fn();
      const onRaise = vi.fn();
      render(<Banner item={budgeted(limit)} onOpenGate={() => {}} onRaise={onRaise} reload={reload} />);
      return { calls, reload, onRaise };
    };

    it("Raise cap opens a dollar editor that patches the policy and retries, never /budget/raise", async () => {
      const { calls, reload, onRaise } = mount({ path: "", key: "budget_usd", value: 5, maximum: 25 });
      await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
      expect(onRaise).not.toHaveBeenCalled();
      expect(screen.getByRole("dialog", { name: "Raise budget cap" })).toHaveTextContent("Now $5. Maximum $25.");
      const input = screen.getByRole("spinbutton");
      expect(screen.getByRole("button", { name: "Save & retry" })).toBeDisabled();
      await userEvent.clear(input);
      await userEvent.type(input, "7.5");
      await userEvent.click(screen.getByRole("button", { name: "Save & retry" }));
      await waitFor(() => expect(reload).toHaveBeenCalled());
      expect(calls.filter((c) => c.method !== "GET")).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { policy: { budget_usd: 7.5 } } }, { method: "POST", path: "/work-items/w1/retry", body: {} }]);
    });

    it("takes cents, and a value that is not above the current cap stays unsaveable", async () => {
      mount({ path: "", key: "budget_usd", value: 0.001, maximum: null });
      await userEvent.click(screen.getByRole("button", { name: "Raise cap" }));
      const input = screen.getByRole("spinbutton");
      await userEvent.clear(input);
      await userEvent.type(input, "0.001");
      expect(screen.getByRole("button", { name: "Save & retry" })).toBeDisabled();
      await userEvent.clear(input);
      await userEvent.type(input, "0.05");
      expect(screen.getByRole("button", { name: "Save & retry" })).toBeEnabled();
    });
  });

  it.each([
    ["escalated", null],
    // An escalation ran on a cap stop and nobody is needed: only the badge (Decisions §4).
    ["escalated", "cap"],
    ["needs_you", "question"],
    ["needs_you", "conflict"],
    ["failed", "failed"],
    ["waiting", "rate_limit"],
  ])("shows nothing for %s (%s)", (display_status, kind) => {
    const { container } = render(<Banner item={detail({ display_status: display_status as never, stop: kind ? stop(kind) : null })} onOpenGate={() => {}} onRaise={() => {}} reload={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("QuestionCard", () => {
  const asked = detail({ display_status: "needs_you", stop: stop("question", { task: "verification.escalation.escalation" }), needs_context_question: "Allow the API change?" });

  it("answers with Send & resume, the answer as the steer", async () => {
    const calls = stubFetch();
    const reload = vi.fn();
    render(<QuestionCard item={asked} compact={false} reload={reload} onOpenThread={() => {}} />);
    expect(screen.getByText("“Allow the API change?”")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your answer"), "Accept the finding.");
    await userEvent.click(screen.getByRole("button", { name: "Send & resume" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/resume", body: { steer: "Accept the finding." } }]);
  });

  it("sends the answer on ⌘↵", async () => {
    const calls = stubFetch();
    const reload = vi.fn();
    render(<QuestionCard item={asked} compact={false} reload={reload} onOpenThread={() => {}} />);
    await userEvent.type(screen.getByLabelText("Your answer"), "Accept the finding.");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/w1/resume", body: { steer: "Accept the finding." } }]);
  });

  it("is one line with Open thread in a node view", async () => {
    const onOpenThread = vi.fn();
    render(<QuestionCard item={asked} compact reload={() => {}} onOpenThread={onOpenThread} />);
    expect(screen.queryByLabelText("Your answer")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("escalation is asking you on verification: “Allow the API change?”");
    await userEvent.click(screen.getByRole("button", { name: "Open thread" }));
    expect(onOpenThread).toHaveBeenCalled();
  });
});
