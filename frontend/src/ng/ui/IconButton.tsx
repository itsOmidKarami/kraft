import { forwardRef, type ButtonHTMLAttributes } from "react";
import { tip } from "./Tooltip";
import "./ui.css";

/** An icon-only action: no stroke, a background on hover and focus. The label
 *  is required, since the icon alone names nothing to a screen reader; it is
 *  also the aria-label and the tooltip. */
export const IconButton = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { label: string }>(function IconButton({ label, className, type = "button", ...rest }, ref) {
  return <button ref={ref} type={type} {...tip(label)} className={`icon-btn${className ? ` ${className}` : ""}`} {...rest} />;
});
