// The description on a page's share card: whole, cut at a full stop, or cut at
// a whole word with an ellipsis, never mid-word.
// Run with: node --experimental-strip-types --test "docsite/tests/*.test.ts"
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { formatOgDescription } from '../app/utils/ogImage.ts'

const cases: [string, string | undefined, string | undefined, string | undefined][] = [
  ['the home page ends on a whole word',
    'Run your coding agent from spec to pull request',
    'A local orchestrator that takes your coding agent from spec to pull request, stopping only when a decision is yours.',
    'A local orchestrator that takes your coding agent from spec to pull request, stopping only when a…'],
  ['a description that fits is kept whole', 'Install', 'Install Kraft with uv or Homebrew.', 'Install Kraft with uv or Homebrew.'],
  ['a full stop inside the budget ends it there',
    'Caps',
    'Kraft caps attempts, time and spend. A cap stops the next agent task from starting, so spend can go over it by the cost of the task already running.',
    'Kraft caps attempts, time and spend.'],
  ['a title over 60 characters counts as 60',
    'A title that is much longer than sixty characters, to check the cap on it',
    'Ninety characters of words with no full stop anywhere in them to cut at so it ends on a word',
    'Ninety characters of words with no full stop anywhere in them to cut at so it ends on a…'],
  ['no description, no text', 'Install', undefined, undefined],
]

for (const [name, title, description, expected] of cases) {
  test(name, () => assert.equal(formatOgDescription(title, description), expected))
}
