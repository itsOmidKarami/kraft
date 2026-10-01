import { BADGES, EXEC_STATES, GATE_STATES, SIZES } from "./gallery.fixtures";
import { NodeGlyph } from "./NodeGlyph";
import { ZoomControls } from "./ZoomControls";
import "./graph.css";

const noop = () => {};

/** /ng/_gallery, unlinked: every graph component and state, from fixtures only. */
export function Gallery() {
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
