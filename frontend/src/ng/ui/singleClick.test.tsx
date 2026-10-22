import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Row } from "../board/Row";
import { NodeGraph } from "../graph/NodeGraph";
import { StageGraph } from "../graph/StageGraph";
import { detail } from "../item/testkit";
import { DOUBLE_CLICK_MS } from "./singleClick";

// test-setup makes a click act at once for every other test; this one pins the wait itself.
vi.unmock("./singleClick");

type On = () => void;
const steps = [{ id: "review", tasks: [{ id: "code_review" }] }];
const loop = { label: "round 1", tasks: [{ id: "mend.fmt", label: "fmt" }, { id: "mend.deps", label: "deps" }, { id: "judge" }] };
const CASES: [string, (select: On, open: On) => ReactElement, RegExp][] = [
  ["board row", (s, o) => <Row item={detail({ display_status: "running", title: "Design the cache" })} selected={false} checked={false} offline={false} now={0} onSelect={s} onOpen={o} onCheck={() => {}} onAction={() => {}} />, /^Design the cache/],
  ["chain node", (s, o) => <StageGraph name="c" nodes={[{ id: "spec", kind: "exec", state: "done" }]} onSelect={s} onFocusNode={o} />, /^spec,/],
  ["node task", (s, o) => <NodeGraph name="v" steps={steps} onSelect={s} onExpand={o} />, /^code_review,/],
  ["node step", (s, o) => <NodeGraph name="v" steps={steps} onSelect={s} onExpand={o} />, /^review, step/],
  ["fix-loop step", (s, o) => <NodeGraph name="v" steps={steps} loop={loop} onSelect={s} onExpand={o} />, /^mend,/],
];

describe("useSingleClick", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it.each(CASES)("%s: a double-click opens without selecting first; a click selects once a second press is overdue, from the keys at once", (_, el, name) => {
    const select = vi.fn(), open = vi.fn();
    render(el(select, open));
    const b = screen.getByRole("button", { name });
    fireEvent.click(b, { detail: 1 });
    fireEvent.click(b, { detail: 2 });
    fireEvent.doubleClick(b, { detail: 2 });
    act(() => vi.advanceTimersByTime(DOUBLE_CLICK_MS));
    expect(open).toHaveBeenCalledTimes(1);
    expect(select).not.toHaveBeenCalled();
    fireEvent.click(b, { detail: 1 });
    act(() => vi.advanceTimersByTime(DOUBLE_CLICK_MS - 1));
    expect(select).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(select).toHaveBeenCalledTimes(1);
    fireEvent.click(b);
    expect(select).toHaveBeenCalledTimes(2);
  });

  // The Gallery and a draft's chain draw the canvases with nothing on double-click: nothing to wait for.
  it.each([
    ["chain node", (s: On) => <StageGraph name="c" nodes={[{ id: "spec", kind: "exec", state: "done" }]} onSelect={s} />, /^spec,/],
    ["node task", (s: On) => <NodeGraph name="v" steps={steps} onSelect={s} />, /^code_review,/],
    ["node step", (s: On) => <NodeGraph name="v" steps={steps} onSelect={s} />, /^review, step/],
    ["fix-loop step", (s: On) => <NodeGraph name="v" steps={steps} loop={loop} onSelect={s} />, /^mend,/],
  ] as const)("%s with no double-click: a click selects at once", (_, el, name) => {
    const select = vi.fn();
    render(el(select));
    fireEvent.click(screen.getByRole("button", { name }), { detail: 1 });
    expect(select).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["a press elsewhere drops it", () => fireEvent.pointerDown(document.body), 0],
    ["a key drops it", () => fireEvent.keyDown(document.body, { key: "Escape" }), 0],
    ["the double-click's own second press keeps it waiting", (b: HTMLElement) => fireEvent.pointerDown(b), 1],
  ] as const)("while a click waits, %s", (_, then, calls) => {
    const select = vi.fn();
    render(CASES[0][1](select, () => {}));
    const b = screen.getByRole("button", { name: CASES[0][2] });
    fireEvent.click(b, { detail: 1 });
    then(b);
    act(() => vi.advanceTimersByTime(DOUBLE_CLICK_MS));
    expect(select).toHaveBeenCalledTimes(calls);
  });
});
