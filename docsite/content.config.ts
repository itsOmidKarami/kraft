// Docus's own collections, minus content/redirects.yml: the map of moved
// addresses, which nuxt.config.ts reads itself. Docus globs every file under
// content/ into its page collections, so left in, the map is parsed as a page
// at /redirects, listed in the sitemap, the search index and llms.txt.
//
// A source's file list is the one place to take it out: its glob closes over
// the options Docus passed, so adding to `exclude` here changes nothing.
// docs.yml fails the build if a /redirects page comes back.
import { defineCollection, z } from '@nuxt/content'
import config from 'docus/content.config'
import { blogDir, draftFiles } from './blog-posts.mjs'

for (const collection of Object.values(config.collections)) {
  for (const source of collection.source ?? []) {
    const getKeys = source.getKeys
    if (getKeys) source.getKeys = async () => (await getKeys()).filter((key) => key !== 'redirects.yml')
  }
}

// The blog's posts (blog-posts.mjs): site code read from docsite/blog/, not
// docsite/content/, so a release tag's build still has main's posts. Its own
// collection, so the docs search, llms.txt and the docs sitemap never see it.
config.collections.blog = defineCollection({
  type: 'page',
  source: { cwd: blogDir, include: '*.md', exclude: ['README.md', ...draftFiles], prefix: '/blog' },
  schema: z.object({
    date: z.string(),
    author: z.string(),
    image: z.string().optional(),
    tags: z.array(z.string()).default([]),
    draft: z.boolean().default(false),
  }),
})

export default config
