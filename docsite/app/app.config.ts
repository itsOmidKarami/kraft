export default defineAppConfig({
  seo: {
    title: 'Kraft',
    description: 'A local orchestrator that takes your coding agent from spec to pull request, stopping only when a decision is yours.',
  },
  header: {
    title: 'Kraft',
  },
  // No `socials.github`: the footer already draws one GitHub icon from
  // `github.url`, and a second entry drew it twice.
  github: {
    owner: 'itsOmidKarami',
    name: 'kraft',
    url: 'https://github.com/itsOmidKarami/kraft',
    branch: 'main',
    rootDir: 'docsite',
  },
  ui: {
    colors: {
      primary: 'violet',
      neutral: 'slate',
    },
    prose: {
      // Nuxt UI soft-wraps code blocks, which breaks YAML indentation on a
      // phone. Scroll sideways instead.
      pre: {
        slots: {
          base: 'whitespace-pre wrap-normal',
        },
      },
      // Seven agent tabs don't fit a phone, and each label truncated to a
      // letter or two ("C.." for both Codex and Cursor). Keep every label
      // whole and let the strip scroll sideways instead. A scrolling list
      // clips its own border, and the selected tab's underline sat on it, so
      // the rule is an inset shadow and the underline sits just inside it.
      tabs: {
        slots: {
          list: 'overflow-x-auto overflow-y-hidden border-b-0 pb-[calc(var(--spacing)+1px)] shadow-[inset_0_-1px_0_var(--ui-border)]',
          indicator: 'bottom-0',
          trigger: 'shrink-0',
        },
      },
    },
  },
})
