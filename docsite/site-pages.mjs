// The marketing pages (app/pages, layouts/site.vue, docsite/DESIGN.md). They
// are site code, not content, so Docus's prerender list, sitemap and llms.txt
// know nothing of them: nuxt.config.ts reads this list for all three, and
// hands it to the app as runtimeConfig.public.sitePages for each page's title
// and description (composables/useSitePage.ts). A new page gets a line here.
export const sitePages = [
  {
    path: '/',
    title: 'Kraft runs the task. You make the calls.',
    description: 'Kraft runs on your own machine. It hands coding tasks to your coding agent, checks the work and stops only for the decisions that are yours.',
  },
  {
    path: '/how-it-works',
    title: 'How Kraft runs a work item.',
    description: 'A work item is one unit of work that ends in one merge request. Kraft runs it through a chain, retries within bounds when a step fails, and hands it to you when a decision is yours.',
  },
  {
    path: '/scale',
    title: 'Run more than one thing at a time.',
    description: 'Split a large plan into slices that several agents build at once, or move one step of a chain to another computer you already have.',
  },
  {
    path: '/why',
    title: 'Not another coding agent.',
    description: 'What Kraft does that an interactive session, a loop or a skill doesn\'t, and when it is the wrong tool.',
  },
  {
    path: '/tour',
    title: 'Where you work with Kraft.',
    description: 'Kraft is one server with several ways in. Each shows the same work items and the same gates, so use whichever is nearest.',
  },
  {
    path: '/customize',
    title: 'The process is yours to change.',
    description: 'Chains are files, so you can edit them, extend them and share them. Pick the agent and the model per task, or run a chain with no server at all.',
  },
]
