import type { ButtonHTMLAttributes } from "react";
import "./ui.css";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" };

/** Decisions §9 Buttons: a background change on hover and focus; danger is
 *  stroked red text (every remove action), never a red fill. */
export function Button({ variant = "secondary", className, type = "button", ...rest }: Props) {
  return <button type={type} className={`btn btn-${variant}${className ? ` ${className}` : ""}`} {...rest} />;
}
