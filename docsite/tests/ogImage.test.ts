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
  ['a version number is not a full stop',
    "What's new in 2.0",
    'What to do before you upgrade from 1.4, and what Kraft 2.0 brings, from a new interface and code-host review to a repo connect that reads more of the repo and safer sharing.',
    'What to do before you upgrade from 1.4, and what Kraft 2.0 brings, from a new interface and code-host review to a repo connect that…'],
  ['a question mark ends a sentence',
    'Why',
    'Why a local orchestrator? It keeps your code, your keys and your agent sessions on your own machine, where you can watch every step and stop any of them at once.',
    'Why a local orchestrator?'],
  // "Ten chars" leaves 141: 140 characters of words and the ellipsis fit exactly.
  ['a word that fits with the ellipsis is kept',
    'Ten chars',
    'abcd '.repeat(27) + 'abcde and more words',
    'abcd '.repeat(27) + 'abcde…'],
  ['no description, no text', 'Install', undefined, undefined],
]

for (const [name, title, description, expected] of cases) {
  test(name, () => assert.equal(formatOgDescription(title, description), expected))
}
