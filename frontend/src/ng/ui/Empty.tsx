import type { ReactNode } from "react";
import "./ui.css";

/** Nothing here. `inline` is a muted line inside a list or pane; `block` is a pane's own empty state and may carry one action under its text. */
export function Empty({ variant = "inline", action, className, children, ...rest }: {
  variant?: "inline" | "block";
  action?: ReactNode;
  className?: string;
  children: ReactNode;
  id?: string;
  role?: "status" | "alert";
}) {
  const cls = `empty empty-${variant}${className ? ` ${className}` : ""}`;
  if (variant === "block") {
    return (
      <div className={cls} {...rest}>
        <p>{children}</p>
        {action}
      </div>
    );
  }
  return <p className={cls} {...rest}>{children}</p>;
}
