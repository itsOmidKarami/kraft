import { useLayoutEffect, useRef, useState } from "react";
import { firstParagraph, hasMoreParagraphs } from "../item/gateRounds";
import { Markdown } from "./Markdown";
import "./ui.css";

/** A round's note (or, with `label`, its review) as a short preview: its first paragraph, rendered as markdown and clipped at three lines. Nothing opens
 *  in place: "Read the note" hands the whole note to the caller's drawer or sheet, and shows only when the note has more
 *  than the preview does (a second paragraph, or text the clip cuts). `onRead` is given the button, which a drawer returns
 *  focus to. */
export function NotePreview({ note, onRead, className, buttonClass = "link-btn", label = "Read the note" }: { note: string; onRead?: (button: HTMLElement) => void; className?: string; buttonClass?: string; label?: string }) {
  const text = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const el = text.current;
    if (!el) return;
    const measure = () => setClipped(el.scrollHeight > el.clientHeight);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, [note]);
  return (
    <div className={className}>
      <div ref={text} className="note-preview"><Markdown text={firstParagraph(note)} /></div>
      {onRead && (clipped || hasMoreParagraphs(note)) && <button type="button" className={buttonClass} onClick={(e) => onRead(e.currentTarget)}>{label}</button>}
    </div>
  );
}
