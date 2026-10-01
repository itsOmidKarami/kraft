import { useRef, useState } from "react";
import { Dialog } from "../ui/Dialog";
import { Popover } from "../ui/Popover";
import { RefreshCw, RotateCw, TriangleAlert, ExternalLink } from "../icons";
import { useApply } from "./store";
import "./apply.css";

/** Waiting to apply (UX V2 W16 A): config saved but not yet running. Reload
 *  rereads files and interrupts nothing; restart is its own button, behind a
 *  confirmation, and absent for a server that was started from a terminal. */
export function ApplyChip() {
  const { restart, reload, managed, phase, error } = useApply();
  const askRestart = useApply((s) => s.askRestart);
  const doReload = useApply((s) => s.runReload);
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const nR = restart.length;
  const nL = reload.length;
  if (nR + nL === 0 && phase !== "restarting") return null;
  const bad = reload.some((i) => i.problem);
  const label = bad ? "Reload found a problem" : nR && nL ? "Waiting to apply" : nR ? "Restart needed" : "Changed on disk";
  const Icon = bad ? TriangleAlert : nR && !nL ? RotateCw : RefreshCw;
  const close = () => {
    setOpen(false);
    button.current?.focus();
  };
  return (
    <>
      <button ref={button} type="button" className={`apply-chip${bad ? " is-bad" : ""}`} aria-haspopup="dialog" aria-expanded={open} aria-label={`${label}, ${nR + nL}`} title={label} onClick={() => setOpen((o) => !o)}>
        <Icon size={14} aria-hidden />
        <span className="apply-chip-label">{label}</span>
        <span className="apply-chip-count">{nR + nL}</span>
      </button>
      <Popover anchor={button} open={open} onClose={close} role="dialog" label="Waiting to apply">
        <div className="apply-pop">
          {nR > 0 && (
            <section className="apply-block" aria-label="Restart needed">
              <div className="apply-block-head"><span>Restart needed</span><span className="apply-muted">interrupts running work</span></div>
              {restart.map((i) => <div key={i.id} className="apply-row"><span className="apply-src">{i.file}</span><span>{i.text}</span></div>)}
              <div className="apply-actions">
                {managed ? (
                  <button type="button" className="apply-btn is-primary" onClick={() => { setOpen(false); void askRestart(); }}><RotateCw size={14} aria-hidden />Restart Kraft</button>
                ) : (
                  <span className="apply-muted">Started in a terminal, so Kraft cannot restart itself. Run kraft admin restart there.</span>
                )}
                {managed && nL > 0 && <span className="apply-muted">Also applies the reload below.</span>}
              </div>
            </section>
          )}
          {nL > 0 && (
            <section className="apply-block" aria-label="Reload">
              <div className="apply-block-head"><span>Reload</span><span className="apply-muted">nothing is interrupted</span></div>
              {reload.map((i) => (
                <div key={i.id} className="apply-row">
                  <span><span className="apply-src">{i.file}</span> {i.text}</span>
                  {i.problem && <span className="apply-problem" role="alert">{i.problem}</span>}
                </div>
              ))}
              <div className="apply-actions">
                <button type="button" className="apply-btn" disabled={phase === "reloading"} onClick={() => void doReload()}><RefreshCw size={14} aria-hidden />{bad ? "Reload again" : "Reload"}</button>
                <span className="apply-muted">rereads the library and policy from disk</span>
              </div>
            </section>
          )}
          {error && <p className="apply-problem" role="alert">{error}</p>}
        </div>
      </Popover>
    </>
  );
}

/** The restart confirmation and the wait for the server to come back; mounted once in the shell. */
export function ApplyDialogs() {
  const { confirming, phase, address, error } = useApply();
  const cancel = useApply((s) => s.cancelRestart);
  const restart = useApply((s) => s.runRestart);
  if (confirming)
    return (
      <Dialog
        title="Restart Kraft?"
        onClose={cancel}
        footer={<><button type="button" className="apply-btn" onClick={cancel}>Cancel</button><button type="button" className="apply-btn is-primary" onClick={() => void restart()}><RotateCw size={14} aria-hidden />Restart</button></>}
      >
        <p>Running work is interrupted while the server restarts. It comes back at <code>{address}</code> and this page follows.</p>
        <p className="apply-muted">same as: kraft admin restart</p>
      </Dialog>
    );
  if (phase === "restarting" || phase === "stuck")
    return (
      <Dialog title={phase === "stuck" ? "Kraft did not come back" : "Restarting Kraft…"} onClose={() => useApply.setState({ phase: "idle" })}>
        <p>{phase === "stuck" ? error : <>Waiting for <code>{address}</code>. This page follows as soon as it answers.</>}</p>
        {phase === "restarting" && address && address !== location.origin && <a className="apply-btn" href={address}><ExternalLink size={14} aria-hidden />Open the new address</a>}
      </Dialog>
    );
  return null;
}
