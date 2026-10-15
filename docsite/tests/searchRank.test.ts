// The order of the docs search: a miniature of the site (a guide, the
// Troubleshooting table, the policy.yaml table, the CLI verbs, an event) run
// through searchSections and Fuse as AppSearch.vue does, one row per query.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { rankHit } from '../app/utils/searchRank.ts'
import { searchSections } from '../app/utils/searchSections.ts'

const Fuse = createRequire(import.meta.url)('fuse.js')

type Node = string | [string, Record<string, unknown>, ...Node[]]
const cell = (tag: string, ...text: Node[]): Node => [tag, {}, ...text]
const code = (text: string): Node => ['code', {}, text]
const table = (header: string[], ...rows: Node[][]): Node => ['table', {}, ['thead', {}, ['tr', {}, ...header.map(h => cell('th', h))]], ['tbody', {}, ...rows.map(row => ['tr', {}, ...row.map(c => cell('td', c))] as Node)]]
const h = (level: number, id: string, text: string): Node => [`h${level}`, { id }, text]
const p = (text: string): Node => ['p', {}, text]
const page = (path: string, title: string, description: string, ...body: Node[]) => ({ path, title, description, body: { value: body } })

const pages = [
  page('/get-started/install', 'Install', 'Install Kraft with uv, Homebrew or from source.', h(2, 'installation-methods', 'Installation methods'), p('Install the package.')),
  page('/get-started/the-real-chain', 'The real chain', 'Run the default chain, approve its gates.', p('A task failed in node reports are in Troubleshooting.')),
  page('/guides/day-to-day/raise-a-cap', 'Raise a cap on a stopped item', 'Raise the budget that stopped a work item.', h(2, 'which-cap', 'Which cap stopped the item'), p('Raise budget with kraft item raise-budget.')),
  page('/troubleshooting/why-did-my-item-stop', 'Why did my item stop?', 'Find the reason a work item stopped.',
    h(2, 'check-the-cause', 'Check the cause before you retry'),
    table(['Command', 'What it shows'], [code('kraft view events ID --type work_item_needs_human'), 'The stop, with every field.']),
    h(2, 'find-your-reason', 'Find your reason'),
    h(3, 'failed-tasks', 'Questions, failed tasks and logins'),
    table(['The reason says', 'What happened', 'Fix'],
      [code('task failed in node <node>: ...'), 'An agent task failed.', 'Read the log, then retry.'],
      [code('needs_context: <question>'), 'The agent asked a question.', 'Answer it.']),
    h(3, 'not-launch', 'A task Kraft would not launch'),
    table(['The reason says', 'What happened', 'Fix'],
      [code('could not start ... with add-on <id> <version> could not be read: ...'), 'The add-on is gone from this machine.', 'See add-on problems.'],
      [code('could not start <task> in node <node>: ...'), 'Kraft refused the launch.', 'Fix the named cause.']),
    h(3, 'caps', 'Caps and budgets'),
    table(['The reason says', 'What happened', 'Fix'],
      [code('budget cap reached: ... on this work item'), 'The dollar cap refused the next launch.', 'Raise it.'],
      [code('budget cap reached: ... today'), 'The daily cap refused the next launch.', 'Raise budget.daily_usd.'])),
  page('/reference/configuration/policy', 'policy.yaml', 'Every field in policy.yaml: caps, budget, concurrency.',
    h(2, 'top-level-keys', 'Top-level keys'),
    table(['Field', 'Type', 'Default', 'Meaning'],
      [code('max_concurrent'), 'integer', '3', 'The concurrency limit: how many work items may be active at once.'],
      [code('budget.work_item_usd'), 'number', 'off', 'Dollar cap per work item.'],
      [code('budget.daily_usd'), 'number', 'off', 'The daily dollar cap for every work item since local midnight.'])),
  page('/reference/http-api/budget', 'Budget', 'Routes for the budget.', h(2, 'raise', 'POST /api/budget/raise'), p('Raise the budget of an item.')),
  page('/reference/cli/item', 'Item verbs', 'Verbs that act on a work item.',
    h(2, 'verbs', 'Verbs at a glance'),
    table(['Verb', 'What it does'], [['a', { href: '#approve' }, code('item approve')], 'Approves the pending gate.'] as never, [['a', { href: '#retry' }, code('item retry')], 'Retries a stopped item.'] as never)),
  page('/reference/events', 'Events', 'Every event.', h(2, 'work_item_needs_human', 'work_item_needs_human'), p('The event behind a stop.')),
]

const items = searchSections(pages as never).map(s => ({ to: s.id, label: s.title, description: '', suffix: s.content }))

function search(term: string) {
  const fuse = new Fuse(items, {
    ignoreLocation: true,
    threshold: 0.1,
    useTokenSearch: true,
    includeScore: true,
    keys: [{ name: 'to', weight: 0.01 }, 'label', 'description', 'suffix'],
    sortFn: (a: never, b: never) => rankHit(a, term) - rankHit(b, term) || (a as { idx: number }).idx - (b as { idx: number }).idx,
  })
  return fuse.search(term, { limit: 40 }).map((hit: { item: { to: string, label: string } }) => hit.item)
}

// [query, how the title of the first hit starts and where it is]
const leads: [string, string, string][] = [
  ['task failed in node', 'task failed in node <node>: ...', '/troubleshooting/why-did-my-item-stop#failed-tasks'],
  ['budget cap reached', 'budget cap reached: ...', '/troubleshooting/why-did-my-item-stop#caps'],
  ['could not start', 'could not start <task> in node <node>: ...', '/troubleshooting/why-did-my-item-stop#not-launch'],
  ['budget.daily_usd', 'budget.daily_usd', '/reference/configuration/policy#top-level-keys'],
  ['max_concurrent', 'max_concurrent', '/reference/configuration/policy#top-level-keys'],
  ['concurrency', 'max_concurrent', '/reference/configuration/policy#top-level-keys'],
  // Loose queries keep the guide first.
  ['raise budget', 'Raise a cap on a stopped item', '/guides/day-to-day/raise-a-cap'],
  ['approve', 'kraft item approve', '/reference/cli/item#approve'],
  ['kraft item retry', 'kraft item retry', '/reference/cli/item#retry'],
  ['needs_human', 'work_item_needs_human', '/reference/events#work_item_needs_human'],
  ['install', 'Install', '/get-started/install'],
]

for (const [query, label, to] of leads) {
  test(`"${query}" leads with ${label}`, () => {
    const [first] = search(query)
    assert.ok(first?.label.startsWith(label), `${first?.label}`)
    assert.equal(first?.to, to)
  })
}

test('a daily cap is found, below the guides', () => {
  assert.ok(search('daily cap').some(hit => hit.label === 'budget.daily_usd'))
})

test('a term the docs never use matches nothing', () => {
  assert.deepEqual(search('budget_exceeded'), [])
})

test('a text that opens with its title does not repeat it', () => {
  const install = searchSections(pages as never).find(s => s.id === '/get-started/install')
  assert.equal(install?.content, 'Kraft with uv, Homebrew or from source.')
})
