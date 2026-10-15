// How a search hit ranks (lower leads); components/app/AppSearch.vue hands
// this to Fuse as its sort function.
// Reference pages are the best match for nearly any word that is also the
// name of a route or a verb ("raise budget" found POST .../budget/raise
// first and the guide ninth), so a Reference hit is ranked as if it scored
// REFERENCE_PENALTY worse, which lets a good hit elsewhere lead but not hide
// it. A query that is itself a route, a flag or a verb (it has a / - _ or .)
// is a search for the reference, so it gets none of what follows either.
// The CLI verbs (utils/searchSections.ts, titled "kraft item approve") are
// exempt, because "approve" should find the command.
// Fuse's own score hardly tells hits apart (every page that has the word
// scores near 0, ties go to the earlier page, and "approve" listed Overview
// first), so it counts for FUSE_WEIGHT of the rank and the rest is how much
// of the query is in the hit's title (more for a verb) and in its text, with a
// page-level hit ahead of its own subsections. The match limit counts hits
// across every section, so at the default 12 a dozen reference rows left no
// room for the guide.
// A query that is the text of a hit's title (an error message, a field name)
// is an exact lookup: the title leads, whichever page it is on.
import { VERB_TITLE } from './searchSections.ts'

export const REFERENCE_PENALTY = 0.55
export const FUSE_WEIGHT = 0.3
export const TITLE_BONUS = 0.2
export const VERB_BONUS = 0.1
export const BODY_BONUS = 0.05
export const PAGE_BONUS = 0.25
export const SECTION_PENALTY = 0.15
// A query typed as it is written in the docs: a phrase in the hit's title, in
// its text, or all of its title. Only the title's own spelling counts, so
// "raise budget" is no phrase of `kraft item raise-budget`, and the guide
// still leads it.
export const PHRASE_BONUS = 0.5
export const TEXT_PHRASE_BONUS = 0.1
export const EXACT_BONUS = 1

export type Hit = { score: number, idx: number, item: Record<number, { v: string }> }

// The fraction of the query's words that appear in some text. A word of seven
// letters or more also counts when its stem does ("concurrency" is in
// "max_concurrent"), and a name's _ - . count as the spaces they stand for.
const stem = (word: string) => word.length > 6 ? word.slice(0, -3) : word
const covers = (text: string, words: string[]) => {
  const spaced = text.replace(/[_\-.]/g, ' ')
  return words.filter(word => text.includes(word) || spaced.includes(stem(word))).length / words.length
}

export function rankHit(hit: Hit, term: string) {
  const [to, label = '', body = ''] = [0, 1, 3].map(i => String(hit.item[i]?.v ?? ''))
  const query = term.toLowerCase().trim().replace(/\s+/g, ' ')
  const words = query.split(' ').filter(Boolean)
  const title = label.toLowerCase().replace(/`/g, '')
  const verb = label.startsWith(VERB_TITLE)
  const reference = /[/\-_.]/.test(query)
  const text = body.toLowerCase()
  // A configuration field is looked up by what it does ("daily cap" for
  // budget.daily_usd), so a row of those pages that has every word of the
  // query, one of them in its field name, is no more a route than a guide is.
  const field = to.startsWith('/reference/configuration/') && words.length > 0 && covers(title, words) > 0 && covers(`${title} ${text}`, words) === 1
  const phrase = query ? (title === query ? EXACT_BONUS : title.includes(query) ? PHRASE_BONUS : 0) + (text.includes(query) ? TEXT_PHRASE_BONUS : 0) : 0
  return FUSE_WEIGHT * hit.score
    + (!reference && to.startsWith('/reference') && !verb && !field ? REFERENCE_PENALTY : 0)
    + (reference ? 0 : to.includes('#') ? SECTION_PENALTY : -PAGE_BONUS)
    - phrase
    + (phrase ? title.length / 500 : 0)
    - (words.length ? (TITLE_BONUS + (verb ? VERB_BONUS : 0)) * covers(title, words) + BODY_BONUS * covers(text, words) : 0)
}
