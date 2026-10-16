import { forwardRef, type SelectHTMLAttributes } from "react";
import "./ui.css";

type Props = SelectHTMLAttributes<HTMLSelectElement> & { mono?: boolean; invalid?: boolean };

/** The one select: a native `<select>` (keyboard, screen readers and the phone's own picker come for free)
 *  at the field height. Name it with a `<label>` around it, `Field`, or `aria-label`. */
export const Select = forwardRef<HTMLSelectElement, Props>(function Select({ mono, invalid, className, ...rest }, ref) {
  return <select ref={ref} className={`ui-select${mono ? " is-mono" : ""}${className ? ` ${className}` : ""}`} aria-invalid={invalid || undefined} {...rest} />;
});
