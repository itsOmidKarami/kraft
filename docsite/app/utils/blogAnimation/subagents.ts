import { type Look, type Meta, seconds, thousands } from './shared.ts'

// The pane needs more height than the chains: three running sub-agents at once.
export const meta: Meta = { loop: 18, still: 9, height: 510 }

// An implement task that opens three sub-agents. The pane lists the running ones with what each is doing now, and
// folds the finished ones away until none is still running; the marks under the task's box follow them.
const START = 0.4
const AGENTS = [
  { from: 3, to: 10.5, name: 'Find every caller of the cache lookup', tools: ['Grep', 'Read', 'Grep', 'Read'], tok: 2600 },
  { from: 3.6, to: 8, name: 'Check the search tests for cache coverage', tools: ['Glob', 'Read', 'Read'], tok: 3100 },
  { from: 4.2, to: 13, name: 'Read how reindex records content hashes', tools: ['Grep', 'Read', 'Bash', 'Read'], tok: 2200 },
]

export function subagentsAt(t: number) {
  const row = (a: typeof AGENTS[number]) => {
    const ran = Math.min(t, a.to) - a.from
    const uses = 1 + Math.floor(ran * 0.8)
    return {
      name: a.name, time: seconds(ran), tokens: `${thousands(4000 + ran * a.tok)} tokens`, uses: `${uses} tool use${uses === 1 ? '' : 's'}`,
      doing: `running ${a.tools[Math.floor(ran / 1.6) % a.tools.length]}`,
    }
  }
  const running = AGENTS.filter(a => t >= a.from && t < a.to).map(row)
  const finished = AGENTS.filter(a => t >= a.to).sort((a, b) => a.to - b.to).map(row)
  return {
    on: t >= START,
    ran: t >= START ? seconds(t - START) : '',
    running,
    finished,
    open: !running.length && finished.length > 0,
    marks: AGENTS.filter(a => t >= a.from).map(a => (t >= a.to ? 'done' : 'run') as Look),
  }
}
