import { Ban, Bell, Bot, Box, ChartColumn, CircleDot, Cog, Download, FileText, GitBranch, GitCompare, GitPullRequest, Inbox, Info, Kanban, Layers, LibraryBig, Lock, Palette, RefreshCw, Scale, ScrollText, Search, Shield, ShieldCheck, Siren, SlidersHorizontal, Sparkles, Terminal, Workflow, type LucideIcon } from "lucide-react";
import { createElement, type ReactElement } from "react";
import { useIconSet } from "./iconSet";

export { Bot, FileText, LocateFixed, Maximize2, Minus, PanelRightClose, PanelRightOpen, Plus, Scan, Siren } from "lucide-react";
// The item page's (ux2-W5).
export { Archive, ArchiveRestore, ChevronDown, ChevronUp, CircleAlert, CircleCheck, CircleHelp, Clock, Copy, EllipsisVertical, Pause, Play, RotateCcw, X } from "lucide-react";
// The apply chip's (ux2-W16).
export { ExternalLink, RefreshCw, RotateCw, TriangleAlert } from "lucide-react";
// The review page's (ux2-W8).
export { ChevronsDownUp, ChevronsUpDown, List, MessageSquare, PanelLeftClose, PanelLeftOpen } from "lucide-react";

/** A task's kind, as the prototype draws it. */
export const KIND_ICON = { agent: Sparkles, builtin: Cog, subprocess: Terminal, forge: GitPullRequest } as const;
export type TaskKind = keyof typeof KIND_ICON;

// ponytail: a static map, not lucide-react/dynamic: dynamic put ~1,860 files and
// ~7.5 MB into the wheel's dist (W3 brief A.6). No shipped chain sets `icon:` yet,
// so this is the prototype's node icons; W10's picker makes its own call.
const NODE_ICONS: Record<string, LucideIcon> = {
  // ux2-W6: a cancelled item's row glyph (AreaBoard draws `ban`).
  ban: Ban, bot: Bot, box: Box, "circle-dot": CircleDot, cog: Cog, "file-text": FileText, "git-branch": GitBranch, "git-compare": GitCompare,
  "git-pull-request": GitPullRequest, inbox: Inbox, layers: Layers, scale: Scale, "scroll-text": ScrollText,
  "refresh-cw": RefreshCw, search: Search, shield: Shield, "shield-check": ShieldCheck, siren: Siren, sparkles: Sparkles, terminal: Terminal, workflow: Workflow,
};

/** A node's or task's own icon (R32): a name outside the static map draws the
 *  kind's icon until the lazy set arrives (W10 K); a name Lucide doesn't have
 *  keeps the kind's icon; an exec node with neither draws a box. */
export function NodeIcon({ name, kind, size }: { name?: string; kind?: TaskKind; size?: number }): ReactElement {
  const inMap = !name || !!NODE_ICONS[name];
  const all = useIconSet(!inMap);
  const icon = (name && (NODE_ICONS[name] ?? all?.[name])) || (kind ? KIND_ICON[kind] : Box);
  return createElement(icon, { size, "aria-hidden": true });
}

/** The sidebar's icons (Decisions §10). */
export const NAV_ICON = {
  search: Search,
  board: Kanban,
  analytics: ChartColumn,
  chains: Workflow,
  library: LibraryBig,
  harnesses: Bot,
  repos: GitBranch,
  policy: SlidersHorizontal,
  intake: Download,
  notifications: Bell,
  access: Lock,
  appearance: Palette,
  about: Info,
} as const;
