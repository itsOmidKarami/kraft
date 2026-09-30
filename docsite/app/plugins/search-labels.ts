// Nuxt UI's English locale has no contentSearch.title or .description, so the
// search dialog's accessible name and description were the raw keys. Supply
// them; `??=` lets a Nuxt UI release that ships its own strings win.
import { en } from '@nuxt/ui/locale'

export default defineNuxtPlugin(() => {
  const messages = en.messages.contentSearch as Record<string, string>
  messages.title ??= 'Search the docs'
  messages.description ??= 'Search every page, then pick a result to open it.'
})
