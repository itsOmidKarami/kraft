import { forwardRef, type ButtonHTMLAttributes } from "react";
import "./ui.css";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" };

/** Decisions §9 Buttons: a background change on hover and focus; danger is
 *  stroked red text (every remove action), never a red fill. Takes a ref, for a
 *  dialog that hands focus to one of its buttons. */
export const Button = forwardRef<HTMLButtonElement, Props>(function Button({ variant = "secondary", className, type = "button", ...rest }, ref) {
  return <button ref={ref} type={type} className={`btn btn-${variant}${className ? ` ${className}` : ""}`} {...rest} />;
});
