import { forwardRef, type AnchorHTMLAttributes, type ButtonHTMLAttributes } from "react";
import { useReason } from "./Reason";
import "./ui.css";

type Look = { strong?: boolean; mono?: boolean; inline?: boolean };
type ButtonProps = Look & ButtonHTMLAttributes<HTMLButtonElement> & { href?: undefined; reason?: string | null };
type AnchorProps = Look & AnchorHTMLAttributes<HTMLAnchorElement> & { href: string };

/** The class list of a link-looking control, for a router `<Link>` that cannot be a LinkButton. */
export function linkButtonClass({ strong, mono, inline }: Look = {}, extra?: string) {
  return `link-btn${strong ? " is-strong" : ""}${mono ? " is-mono" : ""}${inline ? " is-inline" : ""}${extra ? ` ${extra}` : ""}`;
}

/** A text button that reads like a link, for an action inside a line of text. `strong` is the primary one of a
 *  group, `inline` underlines it at rest inside a sentence, `mono` is for an id or a path. With `href` it renders
 *  an `<a>`. `reason` disables it and says why, as text a screen reader finds (useReason). */
export const LinkButton = forwardRef<HTMLButtonElement | HTMLAnchorElement, ButtonProps | AnchorProps>(function LinkButton(props, ref) {
  const { strong, mono, inline, className, ...rest } = props;
  const cls = linkButtonClass({ strong, mono, inline }, className);
  const why = useReason(props.href === undefined ? props.reason : null, { quiet: true });
  if (rest.href !== undefined) return <a ref={ref as React.Ref<HTMLAnchorElement>} className={cls} {...(rest as AnchorHTMLAttributes<HTMLAnchorElement>)} />;
  const { reason: _reason, type = "button", disabled, ...btn } = rest as ButtonProps;
  return (
    <>
      <button ref={ref as React.Ref<HTMLButtonElement>} type={type} className={cls} disabled={disabled || !!why.text} {...why.attrs} {...btn} />
      {why.note}
    </>
  );
});
