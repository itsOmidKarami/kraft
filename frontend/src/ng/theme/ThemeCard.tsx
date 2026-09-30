import type { Accent, ColourAmount, Surface } from "../../types";
import "./themecard.css";

/** A miniature Kraft page in one look (the prototype's ThemeCard). Its data
 *  attributes re-key theme.css inside the card, so it paints any look,
 *  not only the page's. Decorative: the caption beside it says the look. */
export function ThemeCard({ surface, accent, amount, mode }: { surface: Surface; accent: Accent; amount: ColourAmount; mode: "light" | "dark" }) {
  return (
    <div className="theme-card-box">
      <div className="theme-card" data-surface={surface} data-accent={accent} data-amount={amount} data-mode={mode} aria-hidden="true">
        <div className="tc-side">
          <span className="tc-brand">Kraft</span>
          <span className="tc-row tc-row-on">Board</span>
          <span className="tc-row">Chains</span>
          <span className="tc-row">Library</span>
        </div>
        <div className="tc-main">
          <div className="tc-top">
            <span className="tc-crumb">Board › kraft-cb59</span>
            <span className="tc-chip tc-ok">running</span>
          </div>
          <span className="tc-title">Add retry budget</span>
          <div className="tc-chain">
            <span className="tc-node tc-done" />
            <span className="tc-ln" />
            <span className="tc-node tc-done" />
            <span className="tc-ln" />
            <span className="tc-node tc-cur" />
            <span className="tc-ln" />
            <span className="tc-node tc-todo" />
          </div>
          <div className="tc-card">
            <span className="tc-txt">plan_approval is waiting · <span className="tc-link">spec.md</span></span>
            <div className="tc-actions">
              <span className="tc-btn">Approve</span>
              <span className="tc-ghost">Reject</span>
              <span className="tc-chip tc-warn">needs you</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
