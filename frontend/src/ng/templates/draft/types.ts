/** W9's config-draft answers, field for field (docs: /reference/http-api/drafts). */

import type { Plugin } from "../../../types";
import type { Choice } from "../../ui/Combobox";

export type Area = "chains" | "library" | "repos" | "policy" | "intake" | "harnesses";

/** What a config draft edits: an area and its key (a chain id, or `library`). */
export type Scope = { area: Area; key: string };

/** A chain file as its author wrote it, shorthand normalised: every container has `steps`. */
export type Authored = Record<string, unknown>;

export interface Problem {
  path: string;
  field: string | null;
  message: string;
  file: string;
  line: number | null;
  col: number | null;
  fix?: string;
  /** On the library draft: the chain or repo the problem breaks, and the library component it comes from (all `null` for one in `library.yaml` itself). */
  chain?: string | null;
  repo?: string | null;
  component?: string | null;
  /** The settings areas add what each problem is about: a schedule's index (intake), a group and cap level (policy); a repo's path (repos) is `repo`. */
  schedule?: number;
  scope?: string;
  level?: string | null;
}

export interface Change {
  path: string;
  /** `rename` is a chain the draft moved to a new id, at path `""`, with `from` and `to`. */
  kind: "add" | "change" | "remove" | "rename";
  from?: string;
  to?: string;
  summary: string;
  fields?: string[];
  /** On the library draft: the ids of the chains that use the component. */
  reaches?: string[];
  /** The settings areas (`policy`, `intake`) name the file each key row is in. */
  file?: string;
}

export interface Source {
  value: unknown;
  /** `chain`, `library:<section>.<name>`, `policy` or `default`. */
  source: string;
  /** A cap this task's kind does not read, which its `on_failure` recovery runs under. */
  recovery?: boolean;
}

/** `GET /templates/chains/{id}/resolved`'s shape: the chain with `extends` expanded. */
export interface ResolvedView {
  id: string;
  chain: Authored;
  task_paths: string[];
  steering: Record<string, unknown>;
  nodes: { id: string; kind: "exec" | "gate"; icon: string | null }[];
  /** Per gate, the document kinds the exec nodes before it produce. */
  documents: Record<string, string[]>;
}

/** A builtin task's `ref`, a forge task's `target` (`waits`: it runs as an
 *  external wait), an agent task's `inputs`, a policy layer's `grants`. */
export type Choices = Record<"ref" | "inputs" | "grants", Choice[]> & { target: (Choice & { waits: boolean })[] };

export interface Result {
  /** Per file, the last mapping that parsed. */
  model: Record<string, Authored>;
  /** The chain's for `chains`; the `repos`, `policy` and `intake` areas answer their own shape (each page types it). */
  resolved: ResolvedView | null;
  problems: Problem[];
  sources: Record<string, Record<string, Source>>;
  changes: Change[];
  impact: { running?: number; repos?: string[]; chains?: string[] };
  warnings: { file: string; message: string }[];
  policy_values: { auto_escalate_delay_s: number; auto_review_attempts: number };
  /** The closed sets a field takes its values from, by field (`catalogue.choices`). */
  choices?: Choices;
  yaml_error?: { file: string; line: number; col: number; message: string };
}

export interface DraftView {
  area: Area;
  key: string;
  draft: boolean;
  /** Per file, its text: null when the publish deletes it. */
  files: Record<string, string | null>;
  base: Record<string, string | null>;
  /** Each file as it is on disk (null: not there yet): the left side of a YAML diff. */
  published?: Record<string, string | null>;
  updated_at: string | null;
  result: Result;
  /** A chains view of a plugin's chain: every write to it answers 409. */
  plugin?: Plugin | null;
  /** The library view: the loaded plugins' components in `library.yaml`'s shape, by qualified name. Not in `files` or the model. */
  plugin_library?: Record<string, Record<string, Authored>> | null;
}

export interface DraftSummary {
  area: Area;
  key: string;
  files: string[];
  changes: number;
  problems: number;
  updated_at: string;
}

export type Op = { op: string } & Record<string, unknown>;

/** `POST …/ops` adds each op's own answer. */
export type OpsView = DraftView & { ops: { op: string; result?: Record<string, unknown> }[] };

/** The 409 a publish answers when a file changed under the draft. */
export interface StaleBody {
  detail: string;
  files: Record<string, { published: string; draft: string; diff: string }>;
}

export interface RefusalBody {
  detail: string;
  /** The index of the op that failed (422 on ops). */
  op?: number;
  /** A publish refused for problems (422). */
  problems?: Problem[];
  line?: number;
  col?: number;
}
