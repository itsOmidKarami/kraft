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
}

export const SETTINGS_NAV: SettingsNavItem[] = [
  { to: "repos", label: "Repos", icon: GitBranch, group: "how" },
  { to: "chains", label: "Chains", icon: TreeStructure, group: "how" },
  { to: "plugins", label: "Plugins", icon: Plugs, group: "how" },
  { to: "policy", label: "Policy", icon: SlidersHorizontal, group: "how" },
  { to: "steering", label: "Steering", icon: FileText, group: "how" },
  { to: "intake", label: "Auto-intake", icon: DownloadSimple, group: "instance" },
  { to: "notify", label: "Notifications", icon: Bell, group: "instance" },
  { to: "access", label: "Access", icon: LockKey, group: "instance" },
  { to: "appearance", label: "Appearance", icon: Palette, group: "instance" },
];

export const SETTINGS_GROUP_LABEL: Record<SettingsGroup, string> = {
  how: "How work runs",
  instance: "This instance",
};
