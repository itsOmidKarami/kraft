<script setup lang="ts">
// The blog's index (blog-posts.mjs): every published post, newest first. Only
// the build at / draws it; /next/ and /vN/ answer with a redirect to it.
definePageMeta({ layout: 'site', header: false, footer: false })

const home = isHomeBuild(useRuntimeConfig().app.baseURL)
const target = `${useSiteConfig().url.replace(/\/$/, '')}/blog`
const title = 'Writing from the Kraft project.'
const description = 'Releases, design notes and how Kraft gets built, from the people who build it.'

const { data: posts } = await useAsyncData('blog-index', () => queryCollection('blog').order('date', 'DESC').all())

if (home) {
  useSeo({ title, description, type: 'website' })
  defineOgImage('Docs', { headline: 'Kraft', title: 'Blog', description: formatOgDescription('Blog', description) })
  useHead({ link: [{ rel: 'alternate', type: 'application/rss+xml', title: 'Kraft blog', href: `${target}/feed.xml` }] })
} else {
  useRootRedirect(target)
}
</script>

<template>
  <div v-if="home">
    <SitePageHead
      class="s-blog-head"
      label="BLOG"
      :title="title"
    >
      {{ description }}
    </SitePageHead>
    <section class="s-section">
      <div class="s-wrap s-blog-wrap">
        <ol
          v-if="posts?.length"
          class="s-blog-list"
        >
          <li
            v-for="post in posts"
            :key="post.path"
            class="s-blog-entry"
          >
            <BlogMeta :post="post" />
            <h2 class="s-blog-entry-title">
              <NuxtLink :to="post.path">
                {{ post.title }}
              </NuxtLink>
            </h2>
            <p class="s-blog-entry-text">
              {{ post.description }}
            </p>
          </li>
        </ol>
        <p v-else>
          No posts yet.
        </p>
        <p class="s-blog-feed">
          <a :href="`${target}/feed.xml`">RSS feed</a>
        </p>
      </div>
    </section>
  </div>
  <p v-else>
    The blog is at <a :href="target">{{ target }}</a>.
  </p>
</template>
