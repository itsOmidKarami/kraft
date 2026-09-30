import type { ButtonHTMLAttributes } from "react";
import "./ui.css";

/** An icon-only action: no stroke, a background on hover and focus. The label
 *  is required, since the icon alone names nothing to a screen reader. */
export function IconButton({ label, className, type = "button", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return <button type={type} aria-label={label} title={label} className={`icon-btn${className ? ` ${className}` : ""}`} {...rest} />;
}
