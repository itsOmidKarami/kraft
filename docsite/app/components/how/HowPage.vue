<!-- How it works (docsite/DESIGN.md; the How board): the chain, the gates and
     the limits that keep a run in bounds, one capability block each. The copy
     is the approved board's. Its node list is utils/siteChain.ts, held to the
     shipped chain by a test. -->
<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)

const BLOCKS = [
  { id: 'chains', label: 'Chains' },
  { id: 'gates', label: 'Gates' },
  { id: 'independent-checks', label: 'Independent checks' },
  { id: 'caps-and-budgets', label: 'Caps and budgets' },
  { id: 'worktree-isolation', label: 'Worktree isolation' },
  { id: 'ways-in', label: 'Ways in' },
  { id: 'sandbox', label: 'Sandbox' },
]

const WAYS_IN = [
  { name: 'Web UI', text: 'File, start and review on the board.' },
  { name: 'Phone', text: 'The same UI, reached over a tunnel.' },
  { name: 'Command line', code: 'kraft item create' },
  { name: 'Agent session', text: 'Slash commands and MCP tools, from the Kraft plugin.' },
  { name: 'VS Code', text: 'Clear gates beside your code.' },
  { name: 'Trigger', text: 'A schedule or an API call files the item, paused.' },
]

const REPOS_YAML = [
  'repos:',
  '  - path: /home/you/code/my-service',
  '    sandbox:',
  '      kind: docker',
  '      image: ghcr.io/acme/agent@sha256:...',
  '      resources: {cpu: 2, memory: 4g, pids: 512}',
  '      network:',
  '        install: {allow: [registry.npmjs.org]}',
  '        runtime: {allow: [api.github.com]}',
]
</script>

<template>
  <div>
    <SitePageHead
      label="PRODUCT · HOW IT WORKS"
      title="How Kraft runs a work item."
    >
      A work item is one unit of work that ends in one merge request. Kraft runs it through a chain, retries within bounds when a step fails, and hands it to you when a decision is yours.
      <template #picture>
        <figure class="s-figure s-pagehead-picture">
          <SiteVideo
            id="PPr7e5-1yws"
            title="Kraft: stop babysitting your coding agents!"
            length="1:53"
            label="Play the video: Kraft, stop babysitting your coding agents, 1 minute 53 seconds"
          />
          <figcaption>A walkthrough of the chain and its gates, sending work back, a fix loop, the caps, parallel worktrees and the board.</figcaption>
        </figure>
      </template>
    </SitePageHead>

    <SiteOnThisPage :links="BLOCKS" />

    <SiteBlock
      id="chains"
      label="01 · CHAINS"
      title="A process the agent can't skip."
      :docs="{ to: '/get-started/the-real-chain', label: 'The real chain' }"
    >
      <p>Each work item follows a chain, a YAML file you can read and diff.</p>
      <p>Its nodes are steps such as write the spec, implement, verify and open the merge request. Some run your agent, some run a command and some stop for you.</p>
      <template #picture>
        <div class="s-chainlist">
          <div class="s-code-head">
            chains/default.yaml · its 19 nodes, in order
          </div>
          <ol>
            <li
              v-for="(n, i) in DEFAULT_CHAIN_NODES"
              :key="n.id"
              :class="{ 'is-gate': n.gate }"
            >
              <span class="s-chainlist-n">{{ String(i + 1).padStart(2, '0') }}</span>
              <span
                class="s-chainlist-glyph"
                :class="n.gate ? `is-${n.gate}` : ''"
                aria-hidden="true"
              ><i /></span>
              <span class="s-chainlist-id">{{ n.id }}</span>
              <span
                v-if="n.gate === 'you'"
                class="s-chainlist-you"
              >YOU</span>
            </li>
          </ol>
        </div>
        <figcaption>Four gates wait for you. The fifth, on the chain revision, passes on its own when the revision proposes no change.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="gates"
      band
      picture="below"
      label="02 · GATES"
      title="A person at the decisions."
      :docs="{ to: '/guides/day-to-day/review-a-change', label: 'Reviewing a change' }"
    >
      <p>A gate is a node where the chain stops for you. Read what the agent wrote, then approve it, or request changes with a note that re-runs the node that wrote it.</p>
      <p>The default chain waits for you at the spec, at the plan, before it opens a draft merge request, and before it marks it ready.</p>
      <template #picture>
        <SiteShot
          src="/site/gate-review.png"
          :width="1230"
          :height="488"
          alt="A gate's review page: the review brief an agent wrote, the files on the branch and Request changes and Approve buttons"
          :phone="{ src: '/site/gate-review-phone.png', width: 420, height: 290 }"
        />
        <figcaption>A gate's review page: the brief an agent wrote, the files on the branch and the two decisions.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="independent-checks"
      picture="left"
      label="03 · INDEPENDENT CHECKS"
      title="Checks it doesn't grade itself on."
      :docs="{ to: '/reference/chain-nodes/fix-loop', label: 'Fix loop and judge' }"
    >
      <p>Kraft runs your tests in a node of its own, waits for your CI and has a separate reviewer decide whether a fix is done.</p>
      <p>When verification fails, an agent repairs the change and Kraft measures the whole node again. After the first repair, a judge rules whether another attempt is worth it.</p>
      <template #picture>
        <div class="s-drawing s-drawing-roomy">
          <SiteDrawingFixLoop />
        </div>
        <figcaption>In the default chain, this fix loop is limited to two attempts.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="caps-and-budgets"
      band
      label="04 · CAPS AND BUDGETS"
      title="Capped retries and spend."
      :docs="{ to: '/concepts/caps-and-budgets', label: 'Caps and budgets' }"
    >
      <p>Loops limit how often a node repeats. Caps limit the time, tokens and dollars a scope may use.</p>
      <p>Whichever runs out first stops the item and hands it to you, with the full trace, instead of failing it.</p>
      <template #picture>
        <div class="s-drawing s-drawing-nest">
          <SiteDrawingScopeNest />
        </div>
        <figcaption>Scopes nest. The first bound to run out, at any scope, stops the item for you.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="worktree-isolation"
      picture="left"
      label="05 · WORKTREE ISOLATION"
      title="Your checkout stays as you left it."
      :docs="{ to: '/concepts/how-a-work-item-runs', label: 'How a work item runs' }"
    >
      <p>Each item runs on its own branch in its own git worktree, so items run in parallel.</p>
      <p>Archiving an item removes its worktree and its branch.</p>
      <template #picture>
        <div class="s-drawing s-drawing-roomy">
          <SiteDrawingWorktrees />
        </div>
        <figcaption>Each work item has a branch and a worktree of its own. Three run at once by default, and a start past that limit is queued.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="ways-in"
      band
      label="06 · WAYS IN"
      title="File work from wherever you are."
      :docs="{ to: '/guides/day-to-day/file-a-work-item', label: 'File a work item' }"
    >
      <p>You file a work item, and it waits paused until you start it.</p>
      <p>Gates clear from the same places: the board, a phone, VS Code or the command line.</p>
      <template #picture>
        <dl class="s-ways">
          <div
            v-for="w in WAYS_IN"
            :key="w.name"
          >
            <dt>{{ w.name }}</dt>
            <dd>
              <code v-if="w.code">{{ w.code }}</code><template v-else>
                {{ w.text }}
              </template>
            </dd>
          </div>
        </dl>
      </template>
    </SiteBlock>

    <SiteBlock
      id="sandbox"
      picture="left"
      label="07 · SANDBOX"
      title="A container for the agent, when you want one."
      :docs="{ to: '/reference/configuration/sandbox', label: 'Sandboxed workers' }"
    >
      <p>Without a sandbox, workers run directly on your machine, as your user. None is set by default.</p>
      <p>A sandbox runs a repository's workers in a container, with resource limits and a list of hosts they may reach. Only a sandbox with a network policy keeps an agent from deciding its own gates.</p>
      <template #picture>
        <SiteCode
          path="config/repos.yaml"
          note="an example from the docs"
          short="from the docs"
          :lines="REPOS_YAML"
          :hl="[6, 8]"
        />
        <figcaption>The highlighted lines are the network policy: the hosts a task may reach.</figcaption>
      </template>
    </SiteBlock>

    <SiteInstall>
      <template #links>
        <SiteArrowLink to="/tour">
          Next: Product tour
        </SiteArrowLink>
        <SiteArrowLink :to="INSTALL">
          Install guide
        </SiteArrowLink>
        <SiteArrowLink :href="demo">
          Try the live demo
        </SiteArrowLink>
      </template>
    </SiteInstall>
  </div>
</template>
