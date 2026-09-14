import {
  Bell,
  DownloadSimple,
  FileText,
  GitBranch,
  LockKey,
  Palette,
  Plugs,
  SlidersHorizontal,
  TreeStructure,
} from "@phosphor-icons/react";

/**
 * The nine Settings sub-pages, in the order and grouping the sidebar draws
 * them (handoff screen 01). One source of truth: `AppNav` renders this list,
 * `Header` looks up a route's breadcrumb label in it, and
 * `views/settings/index.tsx` builds its `<Routes>` from it — before this
 * module, only `Settings.tsx`'s `PAGES` had this list, and nothing else
 * needed to agree with it.
 *
 * The collapsed rail (screens 03, 11) only ever shows the "how" group's
 * icons, regardless of route — "instance" pages are reachable only via the
 * expanded sidebar. That is Final's own markup, not an oversight to fix.
 */
export type SettingsGroup = "how" | "instance";

export interface SettingsNavItem {
  /** Route segment under `/settings/`. */
  to: string;
  label: string;
  icon: typeof GitBranch;
  group: SettingsGroup;
  /** One line on the Settings index (W7.9). */
  description: string;
}

export const SETTINGS_NAV: SettingsNavItem[] = [
  { to: "repos", label: "Repos", icon: GitBranch, group: "how", description: "Connected repositories, their default chain and test command" },
  { to: "chains", label: "Chains", icon: TreeStructure, group: "how", description: "Chain templates: the nodes, gates and fix loops an item walks" },
  { to: "plugins", label: "Plugins", icon: Plugs, group: "how", description: "What runs for each hook, and with which model" },
  { to: "policy", label: "Policy", icon: SlidersHorizontal, group: "how", description: "Loop caps, concurrency, spend budgets and auto-archive" },
  { to: "steering", label: "Steering", icon: FileText, group: "how", description: "Guidance files that worker sessions read" },
  { to: "intake", label: "Auto-intake", icon: DownloadSimple, group: "instance", description: "Pull ready beads in as work items on a schedule" },
  { to: "notify", label: "Notifications", icon: Bell, group: "instance", description: "Where Kraft tells you an item needs you" },
  { to: "access", label: "Access", icon: LockKey, group: "instance", description: "Password, bind address and signed-in sessions" },
  { to: "appearance", label: "Appearance", icon: Palette, group: "instance", description: "Palette, light or dark, density and board defaults" },
];

export const SETTINGS_GROUP_LABEL: Record<SettingsGroup, string> = {
  how: "How work runs",
  instance: "This instance",
};
