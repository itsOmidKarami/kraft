<!-- A post's date, author and tags, on one line that wraps. -->
<script setup lang="ts">
const props = defineProps<{ post: { date: string, author: string, tags?: string[] } }>()
const day = computed(() => new Date(`${props.post.date}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }))
</script>

<template>
  <p class="s-blog-meta">
    <time :datetime="post.date">{{ day }}</time>
    <span>{{ post.author }}</span>
    <span
      v-for="tag in post.tags"
      :key="tag"
    >{{ tag }}</span>
  </p>
</template>
