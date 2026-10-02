// Nuxt UI's English locale has no contentSearch.title or .description, nor
// header.title or .description, so the search dialog's and the phone nav
// drawer's accessible names and descriptions were the raw keys. Supply them;
// `??=` lets a Nuxt UI release that ships its own strings win.
import { en } from '@nuxt/ui/locale'

export default defineNuxtPlugin(() => {
  const search = en.messages.contentSearch as Record<string, string>
  search.title ??= 'Search the docs'
  search.description ??= 'Search every page, then pick a result to open it.'
  const header = en.messages.header as Record<string, string>
  header.title ??= 'Navigation'
  header.description ??= 'Every page of the docs.'
})
