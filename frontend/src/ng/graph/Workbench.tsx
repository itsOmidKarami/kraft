import { useState } from "react";
import { Button } from "../ui/Button";
import { ChainStrip } from "./ChainStrip";
import { CHAIN_15, CHAIN_15_ARCS, CHAIN_15_SEAMS, NODE_VERIFY, NODE_VERIFY_SIDE, PANE_FACTS } from "./gallery.fixtures";
import { Inspector } from "./Inspector";
import { NodeGraph } from "./NodeGraph";
import { StageGraph } from "./StageGraph";
import { floor, usePaneSelection, type Sel } from "./usePaneSelection";
import { useResizable, useWidth } from "./useResizable";

/** The gallery's item-page stand-in: a chain canvas, its node view, and the
 *  side pane, driven by usePaneSelection. Fixtures only. */
export function Workbench({ page, open = true, width }: { page: string; open?: boolean; width?: number }) {
  const [frame, canvas] = useWidth();
  const size = useResizable(page, canvas, width);
  const [s, dispatch] = usePaneSelection(open);
  const [tab, setTab] = useState("overview");
  const reserve = size.overlay ? 0 : s.open ? size.width : 40;
  const sel = s.sel;
  const node = sel.kind === "chain" ? null : CHAIN_15.find((n) => n.id === sel.node);
  const title = s.sel.kind === "chain" ? "default" : s.sel.kind === "node" ? s.sel.node : s.sel.kind === "step" ? s.sel.step : s.sel.task;
  const crumbs = s.sel.kind === "chain" ? [{ label: "kraft-cb59" }] : [{ label: "default", onClick: () => dispatch({ type: "pick", sel: { kind: "chain" } }) }, ...(s.sel.kind === "task" ? [{ label: s.sel.node, onClick: () => dispatch({ type: "pick", sel: floor(s) }) }, { label: s.sel.step }] : [])];
  const pick = (sel: Sel) => dispatch({ type: "pick", sel });
  const at = s.level === "node" && s.node ? s.node : null;
  const facts = PANE_FACTS[s.sel.kind];

  return (
    <div className="gallery-frame is-tall is-stack" ref={frame}>
      {at && <ChainStrip nodes={CHAIN_15} viewing={at} onOpen={(id) => dispatch({ type: "focus", node: id })} onBack={() => dispatch({ type: "back" })} />}
      <div className="gallery-area">
        {at ? (
          <NodeGraph
            name={at}
            steps={NODE_VERIFY}
            side={NODE_VERIFY_SIDE}
            reserve={reserve}
            selected={s.sel.kind === "task" ? { step: s.sel.step, task: s.sel.task } : s.sel.kind === "step" ? { step: s.sel.step } : undefined}
            onSelect={(x) => pick(x.task ? { kind: "task", node: at, step: x.step, task: x.task } : { kind: "step", node: at, step: x.step })}
            onOpen={(x) => dispatch({ type: "expand", sel: x.task ? { kind: "task", node: at, step: x.step, task: x.task } : { kind: "step", node: at, step: x.step } })}
            onExpand={(x) => dispatch({ type: "expand", sel: x.task ? { kind: "task", node: at, step: x.step, task: x.task } : { kind: "step", node: at, step: x.step } })}
            onEscape={() => dispatch({ type: "escape" })}
            onBackground={() => dispatch({ type: "background" })}
          />
        ) : (
          <StageGraph
            name="default"
            nodes={CHAIN_15}
            arcs={s.sel.kind === "node" && s.sel.node === "review_gate" ? CHAIN_15_ARCS : CHAIN_15_ARCS.filter((a) => a.kind !== "reject")}
            seams={CHAIN_15_SEAMS}
            selected={s.sel.kind === "node" ? s.sel.node : undefined}
            opening="current"
            reserve={reserve}
            onSelect={(id) => pick({ kind: "node", node: id })}
            onOpen={(id) => dispatch({ type: "expand", sel: { kind: "node", node: id } })}
            onFocusNode={(id) => dispatch({ type: "focus", node: id })}
            onEscape={() => dispatch({ type: "escape" })}
            onBackground={() => dispatch({ type: "background" })}
          />
        )}
        <Inspector
          id={`${page}-pane`}
          open={s.open}
          size={size}
          crumbs={crumbs}
          gate={node?.kind === "gate" && s.sel.kind === "node"}
          icon={s.sel.kind === "chain" ? "workflow" : node?.icon}
          title={title}
          sub={sel.kind === "node" && node?.kind === "gate" ? "gate" : facts.sub}
          prob={node?.prob ? { msg: "The run stopped here.", fix: "Raise the cap or retry." } : undefined}
          tabs={[{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }]}
          tab={tab}
          onTab={setTab}
          onCollapse={() => dispatch({ type: "collapse" })}
          onExpand={() => dispatch({ type: "expand" })}
          onFocus={s.level === "chain" && sel.kind === "node" ? () => dispatch({ type: "focus", node: sel.node }) : undefined}
          footer={s.sel.kind === "chain" ? undefined : <><Button>Pause</Button><Button>Skip</Button></>}
        >
          <dl className="pane-facts">
            {facts.rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl>
        </Inspector>
      </div>
    </div>
  );
}
