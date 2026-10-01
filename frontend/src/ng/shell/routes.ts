import type { LucideIcon } from "lucide-react";
import { NAV_ICON } from "../icons";

export type NavGroup = "top" | "templates" | "settings";

export interface NgRoute {
  /** Under the /ng basename. */
  path: string;
  label: string;
  icon: LucideIcon;
  /** The sidebar group; null for a page the sidebar does not list. */
  group: NavGroup | null;
  /** False until a wave builds it: the page is a placeholder linking to its shipped page. */
  built: boolean;
}

/** Every /ng page the shell knows, once. The sidebar, the crumbs, the Go-to
 *  list of the search overlay and the route tree are all built from it. */
export const ROUTES: NgRoute[] = [
  { path: "/", label: "Board", icon: NAV_ICON.board, group: "top", built: true },
  { path: "/analytics", label: "Analytics", icon: NAV_ICON.analytics, group: "top", built: true },
  { path: "/templates/chains", label: "Chains", icon: NAV_ICON.chains, group: "templates", built: true },
  { path: "/templates/library", label: "Library", icon: NAV_ICON.library, group: "templates", built: false },
  { path: "/templates/harnesses", label: "Harnesses", icon: NAV_ICON.harnesses, group: "templates", built: false },
  { path: "/templates/repos", label: "Repos", icon: NAV_ICON.repos, group: "templates", built: false },
  { path: "/settings/policy", label: "Policy", icon: NAV_ICON.policy, group: "settings", built: false },
  { path: "/settings/auto-intake", label: "Auto-intake", icon: NAV_ICON.intake, group: "settings", built: false },
  { path: "/settings/notifications", label: "Notifications", icon: NAV_ICON.notifications, group: "settings", built: false },
  { path: "/settings/access", label: "Access", icon: NAV_ICON.access, group: "settings", built: true },
  { path: "/settings/appearance", label: "Appearance", icon: NAV_ICON.appearance, group: "settings", built: true },
  { path: "/settings/about", label: "About", icon: NAV_ICON.about, group: null, built: false },
  { path: "/archived", label: "Archived", icon: NAV_ICON.board, group: null, built: true },
];

export const routesIn = (group: NavGroup) => ROUTES.filter((r) => r.group === group);
