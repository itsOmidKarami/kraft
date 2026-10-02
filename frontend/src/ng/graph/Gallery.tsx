import { useState } from "react";
import { BADGES, NODE_EDIT, NODE_VERIFY, NODE_VERIFY_SIDE, CHAIN_15, CHAIN_15_ARCS, CHAIN_15_SEAMS, CHAIN_STOPPED, EXEC_STATES, GATE_STATES, SIZES } from "./gallery.fixtures";
import { ChainStrip } from "./ChainStrip";
import { GateView } from "./GateView";
import { NodeGlyph } from "./NodeGlyph";
import { NodeGraph, type NodeSel } from "./NodeGraph";
import { StageGraph } from "./StageGraph";
import { Workbench } from "./Workbench";
import { ZoomControls } from "./ZoomControls";
import "./graph.css";
import "./gallery.css";

const noop = () => {};

/** /_gallery, unlinked: every graph component and state, from fixtures only. */
export function Gallery() {
  const [sel, setSel] = useState("review_gate");
  const [task, setTask] = useState<NodeSel>({ step: "review", task: "code_review" });
  return (
    <main className="gallery">
      <h1>Graph components</h1>
      <section aria-labelledby="g-glyph">
        <h2 id="g-glyph">NodeGlyph</h2>
        {SIZES.map((size) => (
          <div key={size} className="gallery-row">
            {EXEC_STATES.map((state) => (
              <div key={state} className="gallery-cell"><NodeGlyph size={size} state={state} icon="layers" /><span>{`${size} · ${state}`}</span></div>
            ))}
            {GATE_STATES.map((state) => (
              <div key={`g-${state}`} className="gallery-cell"><NodeGlyph kind="gate" size={size} state={state} /><span>{`gate · ${state}`}</span></div>
            ))}
          </div>
        ))}
        <div className="gallery-row">
          {BADGES.map((b) => (
            <div key={b.caption} className="gallery-cell"><NodeGlyph size="lg" kind={b.kind} {...b.item} /><span>{b.caption}</span></div>
          ))}
        </div>
      </section>
      <section aria-labelledby="g-chain">
        <h2 id="g-chain">StageGraph</h2>
        <div className="gallery-frame">
          <StageGraph name="default" nodes={CHAIN_15} selected={sel} onSelect={setSel} onOpen={setSel} arcs={sel === "review_gate" ? CHAIN_15_ARCS : CHAIN_15_ARCS.filter((a) => a.kind !== "reject")} seams={CHAIN_15_SEAMS} />
        </div>
        <div className="gallery-frame">
          <StageGraph name="stopped" nodes={CHAIN_STOPPED} opening="current" />
        </div>
      </section>
      <section aria-labelledby="g-node">
        <h2 id="g-node">NodeGraph</h2>
        <div className="gallery-frame is-tall is-stack">
          <ChainStrip nodes={CHAIN_15} viewing="verification" onOpen={setSel} />
          <div className="gallery-area">
          <NodeGraph name="verification" steps={NODE_VERIFY} selected={task} onSelect={setTask} onOpen={setTask} side={NODE_VERIFY_SIDE} loop={{ tone: "active", label: "fix loop · attempt 2 of 3" }} onFailure="retry_flaky, then re-measure" />
          </div>
        </div>
        <div className="gallery-frame">
          <NodeGraph name="security_scan" steps={NODE_EDIT} seamAfter />
        </div>
      </section>
      <section aria-labelledby="g-gate">
        <h2 id="g-gate">GateView</h2>
        <div className="gallery-frame is-tall">
          <GateView
            gate={{ id: "review_gate", state: "current" }}
            reviewer={{ id: "auto_review", state: "done", chip: "approve · 2 notes", chipTone: "green" }}
            message="Read the review notes, then approve to open the merge request."
            doc={{ label: "review.md", onClick: noop }}
            reject={{ id: "implement", icon: "layers", onClick: noop }}
            note="Approving moves the item to merge_request. Rejecting sends it back to implement with your note."
          />
        </div>
        <div className="gallery-frame">
          <GateView gate={{ id: "approve_plan", state: "plain" }} onAdd={noop} />
        </div>
      </section>
      <section aria-labelledby="g-pane">
        <h2 id="g-pane">Inspector</h2>
        <Workbench page="gallery" />
        <Workbench page="gallery-rail" open={false} />
        <Workbench page="gallery-wide" width={520} />
      </section>
      <section aria-labelledby="g-zoom">
        <h2 id="g-zoom">ZoomControls</h2>
        <div className="gallery-row">
          <div className="gallery-cell"><ZoomControls scale={1} mode="current" onIn={noop} onOut={noop} onReset={noop} onFit={noop} onCurrent={noop} /><span>current, 100%</span></div>
          <div className="gallery-cell"><ZoomControls scale={0.62} mode="fit" onIn={noop} onOut={noop} onReset={noop} onFit={noop} /><span>fitted, no current node</span></div>
        </div>
      </section>
    </main>
  );
}
