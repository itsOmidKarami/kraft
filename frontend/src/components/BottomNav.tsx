import { NavLink } from "react-router-dom";
import { ChartBar, GearSix, MagnifyingGlass, SquaresFour } from "@phosphor-icons/react";

/**
 * The phone app shell's tab bar (mobile app shell design, §1). Desktop keeps
 * the header's Analytics/Settings links and Search button (`App.tsx`'s `Nav`,
 * now `.desktop-only`); this is the phone equivalent, fixed to the bottom.
 */
const TABS: { to: string; label: string; icon: typeof SquaresFour; end?: boolean }[] = [
  { to: "/", label: "Board", icon: SquaresFour, end: true },
  { to: "/search", label: "Search", icon: MagnifyingGlass },
  { to: "/analytics", label: "Analytics", icon: ChartBar },
  { to: "/settings", label: "Settings", icon: GearSix },
];

export function BottomNav() {
  return (
    <nav className="bottom-nav" aria-label="primary">
      {TABS.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} className="bottom-nav-tab">
          <Icon size={22} />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
