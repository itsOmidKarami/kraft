// The default chain's nodes as How it works lists them: `you` is a gate that
// waits for a person, `auto` the gate that passes on its own when the chain
// revision proposes no change. docsite/tests/siteChain.test.ts holds this to
// config/chains/default.yaml, so the page cannot drift from the chain it
// describes.
export type ChainNode = { id: string, gate?: 'you' | 'auto' }

export const DEFAULT_CHAIN_NODES: ChainNode[] = [
  { id: 'spec' },
  { id: 'spec_approval', gate: 'you' },
  { id: 'plan' },
  { id: 'plan_approval', gate: 'you' },
  { id: 'chain_revision' },
  { id: 'chain_revision_approval', gate: 'auto' },
  { id: 'implementation' },
  { id: 'verification' },
  { id: 'work_brief' },
  { id: 'local_review', gate: 'you' },
  { id: 'describe_merge_request' },
  { id: 'draft_merge_request' },
  { id: 'merge_request_feedback' },
  { id: 'work_item_summary' },
  { id: 'final_review', gate: 'you' },
  { id: 'mark_ready' },
  { id: 'external_approval' },
  { id: 'merge' },
  { id: 'post_merge_ci' },
]
