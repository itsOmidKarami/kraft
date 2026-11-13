<!-- Make it yours: the process is yours to change and share. Not on the
     boards: the block template, its copy drawn from the docs each block links
     to. Its two screenshots are the README's until they are retaken. -->
<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)

// The review `kraft admin addon install` prints, as the docs show it.
const ADDON_REVIEW = [
  'release@acme new, 1.0.0',
  'Reach:',
  '  - release:ship.nodes[implementation].tasks[implement]: harness set to \'claude\'',
  '  - release:ship.nodes[implementation].tasks[implement].policy: limit time_cap_minutes set, set to 90',
  '  …',
  'Content:',
  '  - release:ship.nodes[implementation].tasks[implement]: prompt new:',
  'Implement the approved plan, then write the release notes.',
  '  …',
  'Apply release@acme? [y/N]',
]
const LITE_INSTALL = [
  'claude plugin marketplace add itsOmidKarami/kraft',
  'claude plugin install kraft-lite@kraft',
]
</script>

<template>
  <div>
    <SitePageHead
      label="PRODUCT · MAKE IT YOURS"
      title="The process is yours to change."
    >
      Chains are files, so you can edit them, extend them and share them. Pick the agent and the model per task, or run a chain with no server at all.
    </SitePageHead>

    <SiteBlock
      id="your-own-chains"
      picture="below"
      label="01 · YOUR OWN CHAINS"
      title="Change one node, keep the rest."
      :docs="{ to: '/guides/customize/write-your-own-chain', label: 'Write your own chain' }"
    >
      <p>A chain that extends <code>default</code> holds only what it changes. Every other node is read from <code>default</code>, so your chain keeps up with it.</p>
      <p>The Chains screen draws it as a graph. Review &amp; publish shows what changed and checks it before it takes effect.</p>
      <template #picture>
        <SiteShot
          src="/site/chain-extends.png"
          :width="1230"
          :height="700"
          alt="The Chains screen: a chain that extends default, drawn as a graph, with its verification node marked overridden here and a security scan node marked added here"
          :phone="{ src: '/site/chain-extends-phone.png', width: 340, height: 100 }"
        />
        <figcaption>A chain that extends <code>default</code>, drawn as a graph.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="add-ons"
      band
      label="02 · ADD-ONS"
      title="Share a process as a package."
      :docs="{ to: '/guides/customize/share-chains-with-addons', label: 'Share chains with add-ons' }"
    >
      <p>An add-on is a directory of chains, library components, skills and agent profiles, installed from a collection.</p>
      <p>Install shows a review of what it reaches before anything is written. An instance pins it to a commit and changes it only through an update you review.</p>
      <template #picture>
        <SiteCode
          path="kraft admin addon install release@acme"
          note="the review, from the docs"
          short="from the docs"
          :lines="ADDON_REVIEW"
          :hl="[1, 4]"
        />
        <figcaption>The review Kraft prints before an add-on is installed.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="several-agents"
      picture="below"
      label="03 · SEVERAL AGENTS"
      title="The agent and model, per task."
      :docs="{ to: '/reference/harnesses', label: 'Agent harnesses' }"
    >
      <p>Each agent task names a harness and a model. Kraft ships seven harnesses: Claude Code, Codex, Cursor, OpenCode, Antigravity, Gemini CLI and Amp.</p>
      <p>Every shipped task runs on Claude Code, so moving one to another harness is a chain edit.</p>
      <template #picture>
        <SiteShot
          src="/site/harnesses.png"
          :width="1230"
          :height="700"
          alt="Settings, Harnesses: each harness with whether it is installed and available, and the agent tasks that run on it"
          :phone="{ src: '/site/harnesses-phone.png', width: 370, height: 248 }"
        />
        <figcaption>Harnesses in Settings, with the tasks each one runs.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="kraft-lite"
      band
      label="04 · KRAFT LITE"
      title="The chain, with no server."
      :docs="{ to: '/guides/kraft-lite', label: 'Kraft Lite' }"
    >
      <p>Kraft Lite runs a chain inside one agent session. It starts no server, port or database, and needs Python 3.10 or newer and nothing else.</p>
      <p>It reads the same chain files and walks the same nodes, gates and fix-loop caps. It resumes across sessions but doesn't outlive your terminal.</p>
      <template #picture>
        <SiteCode
          path="Claude Code"
          note="installing Kraft Lite, from the docs"
          short="from the docs"
          :lines="LITE_INSTALL"
        />
        <figcaption>Lite ships in the same plugin marketplace as Kraft's own plugin.</figcaption>
      </template>
    </SiteBlock>

    <SiteInstall>
      <template #links>
        <SiteArrowLink to="/scale">
          Next: Scale up
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
