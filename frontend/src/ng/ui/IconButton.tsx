import { forwardRef, type ButtonHTMLAttributes } from "react";
import "./ui.css";

/** An icon-only action: no stroke, a background on hover and focus. The label
 *  is required, since the icon alone names nothing to a screen reader. */
export const IconButton = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { label: string }>(function IconButton({ label, className, type = "button", ...rest }, ref) {
  return <button ref={ref} type={type} aria-label={label} title={label} className={`icon-btn${className ? ` ${className}` : ""}`} {...rest} />;
});
