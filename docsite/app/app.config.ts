export default defineAppConfig({
  seo: {
    title: 'Kraft',
    description: 'A local orchestrator that runs your coding agent from spec to pull request, each task in its own git worktree, and stops only when a decision is yours.',
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
    },
  },
})
