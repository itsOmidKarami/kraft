import { useRef, useState, type ReactNode } from "react";
import type { CompareFile, CompareTarget, WorkItem } from "../../types";
import { ChevronDown, ChevronsDownUp, ChevronsUpDown, List, PanelLeftClose, PanelLeftOpen } from "../icons";
import { IconButton } from "../ui/IconButton";
import { Menu } from "../ui/Menu";
import { Popover } from "../ui/Popover";
import { fromOptions, nodeRows, targetLabel, toOptions, type TargetOption } from "./model";
import type { DiffPrefs } from "./prefs";
import type { ReviewPlace } from "./url";

type Item = Pick<WorkItem, "attempts" | "last_review_sha" | "chain_definition">;

/** The row above the diff (prototype 352–410). */
export function Toolbar(p: {
  item: Item;
  place: ReviewPlace;
  setPlace: (patch: Partial<ReviewPlace>) => void;
  /** Every file of the comparison, before the nodes filter. */
  files: CompareFile[];
  treeOpen: boolean;
  onTree: () => void;
  onCollapseAll: () => void;
  onExpandAll: () => void;
  prefs: DiffPrefs;
  setPrefs: (patch: Partial<DiffPrefs>) => void;
}) {
  return (
    <div className="rv-toolbar" role="toolbar" aria-label="Review">
      <IconButton label={p.treeOpen ? "Collapse file list" : "Expand file list"} aria-expanded={p.treeOpen} className="rv-tree-toggle" onClick={p.onTree}>
        {p.treeOpen ? <PanelLeftClose size={16} aria-hidden /> : <PanelLeftOpen size={16} aria-hidden />}
      </IconButton>
      <span className="rv-compare">
        Compare
        <TargetPicker heading="Compare from" value={p.place.from} options={fromOptions(p.item)} item={p.item} onPick={(from) => p.setPlace({ from })} />
        and
        <TargetPicker heading="Compare to" value={p.place.to} options={toOptions(p.item)} item={p.item} onPick={(to) => p.setPlace({ to })} />
      </span>
      <NodesFilter files={p.files} chainOrder={p.item.chain_definition.nodes.map((n) => n.id)} nodes={p.place.nodes} onChange={(nodes) => p.setPlace({ nodes })} />
      <span className="rv-spacer" />
      <IconButton label="Collapse all" onClick={p.onCollapseAll}><ChevronsDownUp size={16} aria-hidden /></IconButton>
      <IconButton label="Expand all" onClick={p.onExpandAll}><ChevronsUpDown size={16} aria-hidden /></IconButton>
      <DiffSettings prefs={p.prefs} set={p.setPrefs} />
    </div>
  );
}

function TargetPicker({ heading, value, options, item, onPick }: { heading: string; value: CompareTarget; options: TargetOption[]; item: Item; onPick: (t: CompareTarget) => void }) {
  const shown = targetLabel(value, item);
  return (
    <Menu
      label={heading}
      heading={heading}
      triggerClass="rv-word"
      // A text trigger is named by its text (W1 Menu): the heading rides along, hidden.
      trigger={<><span className="review-visually-hidden">{heading}</span>{" "}{shown}<ChevronDown size={12} aria-hidden /></>}
      items={options.map((o) => ({ label: o.label, sub: o.sub, disabled: o.disabled, checked: o.value === value, onSelect: () => o.value && onPick(o.value) }))}
    />
  );
}

/** A box in a checklist: ticked, unticked, or mixed (All with some nodes off). */
const Box = ({ on }: { on: boolean | "mixed" }) => <span className={`rv-box${on ? " is-on" : ""}`} aria-hidden="true">{on === "mixed" ? "−" : on ? "✓" : ""}</span>;

/** A button that opens a checklist: a menu of checkbox and radio items that
 *  stays open while you tick them. Popover moves focus to the first item and
 *  gives ↑/↓; Escape closes it back to the button. */
function Pop({ label, trigger, triggerClass, children }: { label: string; trigger: ReactNode; triggerClass: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const close = () => {
    setOpen(false);
    anchor.current?.focus();
  };
  return (
    <>
      <button ref={anchor} type="button" className={triggerClass} aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {trigger}
      </button>
      <Popover anchor={anchor} open={open} onClose={close} role="menu" label={label}>
        <div className="rv-checklist">{children}</div>
      </Popover>
    </>
  );
}

/** All nodes, or some (prototype 386–397). Client-side on `touched_by`. */
export function NodesFilter({ files, chainOrder, nodes, onChange }: { files: CompareFile[]; chainOrder: string[]; nodes: string[] | null; onChange: (nodes: string[] | null) => void }) {
  const rows = nodeRows(files, chainOrder);
  const on = (id: string) => nodes === null || nodes.includes(id);
  const picked = rows.filter((r) => on(r.id)).length;
  const all = picked === rows.length ? true : picked === 0 ? false : "mixed";
  const kept = nodes === null ? files.length : files.filter((f) => f.touched_by.some((n) => nodes.includes(n))).length;
  const label = all === true ? "All nodes" : `${picked} of ${rows.length} nodes`;
  const toggle = (id: string) => {
    const next = rows.filter((r) => (r.id === id ? !on(r.id) : on(r.id))).map((r) => r.id);
    onChange(next.length === rows.length ? null : next);
  };
  return (
    <Pop label={`Nodes: ${label}`} triggerClass="rv-pill" trigger={<>{label}<ChevronDown size={12} aria-hidden /></>}>
      <button type="button" role="menuitemcheckbox" aria-checked={all} tabIndex={-1} className="rv-check" onClick={() => onChange(all === true ? [] : null)}>
        <Box on={all} />
        <span className="rv-check-label">All nodes</span>
        <span className="rv-check-count">{kept} of {files.length} files</span>
      </button>
      <span className="rv-check-rule" role="separator" />
      {rows.map((r) => (
        <button key={r.id} type="button" role="menuitemcheckbox" aria-checked={on(r.id)} tabIndex={-1} className="rv-check" onClick={() => toggle(r.id)}>
          <Box on={on(r.id)} />
          <span className="rv-check-label rv-mono">{r.id}</span>
          <span className="rv-check-count">{r.files} {r.files === 1 ? "file" : "files"}</span>
        </button>
      ))}
    </Pop>
  );
}

/** Layout and the diff's switches (prototype 401–409, Decisions §13), saved to `theme.yaml`. */
export function DiffSettings({ prefs, set }: { prefs: DiffPrefs; set: (patch: Partial<DiffPrefs>) => void }) {
  const toggles: [keyof DiffPrefs, string][] = [
    ["show_whitespace", "Show whitespace changes"],
    ["one_file_at_a_time", "Show one file at a time"],
    ["word_highlight", "Highlight changed words"],
    ["wrap_lines", "Wrap long lines"],
  ];
  return (
    <Pop label="Diff settings" triggerClass="icon-btn rv-settings" trigger={<><List size={16} aria-hidden /><ChevronDown size={12} aria-hidden /></>}>
      <span className="menu-heading" aria-hidden="true">Compare changes</span>
      {(["split", "unified"] as const).map((l) => (
        <button key={l} type="button" role="menuitemradio" aria-checked={prefs.layout === l} tabIndex={-1} className="rv-check" onClick={() => set({ layout: l })}>
          <span className="rv-tick" aria-hidden="true">{prefs.layout === l ? "✓" : ""}</span>
          <span className="rv-check-label">{l === "split" ? "Side-by-side" : "Inline"}</span>
        </button>
      ))}
      <span className="rv-check-rule" role="separator" />
      {toggles.map(([k, text]) => (
        <button key={k} type="button" role="menuitemcheckbox" aria-checked={!!prefs[k]} tabIndex={-1} className="rv-check" onClick={() => set({ [k]: !prefs[k] })}>
          <Box on={!!prefs[k]} />
          <span className="rv-check-label">{text}</span>
        </button>
      ))}
    </Pop>
  );
}
