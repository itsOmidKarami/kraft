<script setup lang="ts">
// One blog post (blog-posts.mjs). Only the build at / writes it; /next/ and
// /vN/ have only the index's redirect. Its canonical names this address, so a
// copy shared on another site can name it as the original.
definePageMeta({ layout: 'site', header: false, footer: false })

const route = useRoute()
const home = isHomeBuild(useRuntimeConfig().app.baseURL)
const origin = useSiteConfig().url.replace(/\/$/, '')
const target = `${origin}${route.path}`

const { data: post } = await useAsyncData(route.path, () => queryCollection('blog').path(route.path).first())
if (!post.value) throw createError({ statusCode: 404, statusMessage: 'No post at this address', fatal: true })

if (home) {
  const { title, description, date, image } = post.value
  useSeo({ title, description, type: 'article', publishedAt: date, ogImage: image ? `${origin}${image}` : undefined })
  useSeoMeta({ author: post.value.author, articleAuthor: [post.value.author], articleTag: post.value.tags })
  useHead({ link: [{ rel: 'alternate', type: 'application/rss+xml', title: 'Kraft blog', href: `${origin}/blog/feed.xml` }] })
  if (!image) defineOgImage('Docs', { headline: 'Kraft blog', title, description: formatOgDescription(title, description) })
} else {
  useRootRedirect(target)
}
</script>

<template>
  <article v-if="home && post">
    <SitePageHead
      class="s-blog-head"
      label="BLOG"
      :title="post.title"
    >
      <p>{{ post.description }}</p>
      <BlogMeta
        :post="post"
        class="s-blog-head-meta"
      />
    </SitePageHead>
    <section class="s-section">
      <div class="s-wrap s-blog-wrap">
        <ContentRenderer
          class="s-blog-body"
          :value="post"
        />
        <p class="s-blog-back">
          <NuxtLink to="/blog">
            All posts
          </NuxtLink>
        </p>
      </div>
    </section>
  </article>
  <p v-else>
    This post is at <a :href="target">{{ target }}</a>.
  </p>
</template>
