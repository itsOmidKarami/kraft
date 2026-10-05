// Docus's own collections, minus content/redirects.yml: the map of moved
// addresses, which nuxt.config.ts reads itself. Docus globs every file under
// content/ into its page collections, so left in, the map is parsed as a page
// at /redirects, listed in the sitemap, the search index and llms.txt.
//
// A source's file list is the one place to take it out: its glob closes over
// the options Docus passed, so adding to `exclude` here changes nothing.
// docs.yml fails the build if a /redirects page comes back.
import config from 'docus/content.config'

for (const collection of Object.values(config.collections)) {
  for (const source of collection.source ?? []) {
    const getKeys = source.getKeys
    if (getKeys) source.getKeys = async () => (await getKeys()).filter((key) => key !== 'redirects.yml')
  }
}

export default config
