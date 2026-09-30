import { Cog, GitPullRequest, Sparkles, Terminal } from "lucide-react";

/** A task's kind, as the prototype draws it. */
export const KIND_ICON = { agent: Sparkles, builtin: Cog, subprocess: Terminal, forge: GitPullRequest } as const;
