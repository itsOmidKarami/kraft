import { forwardRef, type ButtonHTMLAttributes } from "react";
import "./ui.css";

/** The class of a chip, for a trigger that is not a Chip itself (a Menu button, a label around a field). */
export const chipClass = (extra?: string) => (extra ? `chip ${extra}` : "chip");

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { pressed?: boolean; changed?: boolean };

/** A toggle you press: a filter, an on/off choice, one option of a radio group. `pressed` fills it
 *  (aria-pressed, or aria-checked when `role="radio"`); `changed` marks it as edited in the draft.
 *  A read-only label is a Badge. */
export const Chip = forwardRef<HTMLButtonElement, Props>(function Chip({ pressed, changed, className, type = "button", role, ...rest }, ref) {
  const state = pressed === undefined ? {} : role === "radio" ? { "aria-checked": pressed } : { "aria-pressed": pressed };
  return <button ref={ref} type={type} role={role} className={["chip", changed && "is-changed", className].filter(Boolean).join(" ")} {...state} {...rest} />;
});
