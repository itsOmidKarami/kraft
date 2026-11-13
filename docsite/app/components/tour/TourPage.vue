<!-- Product tour: where you work with Kraft, one surface per block. Not on
     the boards: the block template's wide variant, its copy drawn from the
     docs each block links to. The screenshots are crops of the README's, with
     the app's sidebar cut off, until they are retaken. -->
<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)

const SKILLS = [
  { name: '/kraft:handoff', text: 'File work you settled in this session.' },
  { name: '/kraft:board', text: 'What is running, blocked or waiting on you.' },
  { name: '/kraft:status', text: 'Where one work item has got to.' },
  { name: '/kraft:review', text: 'Read a gate\'s document and recommend a call.' },
  { name: '/kraft:gates', text: 'Approve, reject or skip a gate.' },
  { name: '/kraft:steer', text: 'Pause, resume or redirect a work item.' },
  { name: '/kraft:triage', text: 'Why an item stopped, and how to restart it.' },
]
</script>

<template>
  <div>
    <SitePageHead
      label="PRODUCT · TOUR"
      title="Where you work with Kraft."
    >
      Kraft is one server with several ways in. Each shows the same work items and the same gates, so use whichever is nearest.
    </SitePageHead>

    <SiteBlock
      id="board"
      picture="below"
      label="01 · BOARD"
      title="Every work item, grouped by what it needs."
      :docs="{ to: '/reference/web-ui/board', label: 'The board' }"
    >
      <p>The board lists your work items in four groups: Needs you, Running, Not started and Done. Needs you is always first.</p>
      <p>A row says why it's where it is and carries one button, such as Review, Answer, Retry or Raise cap. Filter by chain, gate or stop reason.</p>
      <template #picture>
        <SiteShot
          src="/site/tour-board.png"
          :width="1230"
          :height="700"
          alt="The Kraft board: work items under Needs you, each with the reason it stopped and one button, and Running below"
          :phone="{ src: '/site/tour-board-phone.png', width: 400, height: 260 }"
        />
        <figcaption>The board, with Needs you first and one button on each row.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="item-page"
      band
      picture="below"
      label="02 · ITEM PAGE"
      title="One item, with its chain drawn out."
      :docs="{ to: '/reference/web-ui/item-page', label: 'An item\'s page' }"
    >
      <p>An item's page shows the brief and the chain as a graph of named nodes. Select a node, a gate or a task and the inspector shows its detail.</p>
      <p>Each state has one main action: Start, Pause, Resume, Review or Retry from the stopped task. A banner says when the item is waiting for you.</p>
      <template #picture>
        <SiteShot
          src="/site/tour-item.png"
          :width="1230"
          :height="700"
          alt="A work item's page: its brief, a banner waiting for approval, the chain as a graph with the final review gate selected, and the inspector beside it"
          :phone="{ src: '/site/tour-item-phone.png', width: 420, height: 170 }"
        />
        <figcaption>An item's page, with the chain as a graph and a gate selected.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="review-page"
      picture="below"
      label="03 · REVIEW PAGE"
      title="The diff, with your review along the bottom."
      :docs="{ to: '/reference/web-ui/review-page', label: 'The review page' }"
    >
      <p>The review page shows an item's diff with a review bar beneath it. Comment on a line, mark files Viewed, then request changes or approve.</p>
      <p>At a gate it opens on the gate's document. Compare from the base, an earlier attempt or your last review, to read only what changed.</p>
      <template #picture>
        <SiteShot
          src="/site/tour-review.png"
          :width="1230"
          :height="700"
          alt="The review page: the changed files, a diff with a comment being written on a line, and Request changes and Approve along the bottom"
          :phone="{ src: '/site/tour-review-phone.png', width: 410, height: 240 }"
        />
        <figcaption>A comment on a changed line, above Request changes and Approve.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="phone"
      band
      label="04 · PHONE"
      title="The same board, narrower."
      :docs="{ to: '/guides/day-to-day/kraft-on-a-phone', label: 'Kraft on a phone' }"
    >
      <p>Below 768 pixels the board switches to a phone layout at the same address. A card that needs you carries its own buttons, such as Approve and Request changes.</p>
      <p>Set up a webhook or browser alerts and Kraft tells you when an item waits at a gate or stops.</p>
      <template #picture>
        <div class="s-crop s-crop-phone">
          <img
            src="/site/mobile.png"
            width="390"
            height="844"
            alt="The Kraft board at phone width, with work items under Needs you as cards"
            loading="lazy"
          >
        </div>
        <figcaption>The board at phone width.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="analytics"
      picture="below"
      label="05 · ANALYTICS"
      title="Where the time and the money went."
      :docs="{ to: '/reference/web-ui/analytics', label: 'Analytics' }"
    >
      <p>Analytics rolls up lead time, cost, repairs per item and throughput across your work items. Filter by range, repo or chain.</p>
      <p>It keeps time waiting on you apart from run time, so a slow reviewer doesn't read as a slow agent.</p>
      <template #picture>
        <SiteShot
          src="/site/tour-analytics.png"
          :width="1230"
          :height="500"
          alt="Analytics for the last 8 weeks: items completed, lead time, time waiting on you, cost and repairs per item, throughput by week and the wait at each gate"
          :phone="{ src: '/site/tour-analytics-phone.png', width: 410, height: 182 }"
        />
        <figcaption>Lead time, waiting on you, cost and repairs for the last 8 weeks.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="vs-code"
      band
      picture="below"
      label="06 · VS CODE"
      title="The board in your sidebar."
      :docs="{ to: '/guides/day-to-day/vscode', label: 'Kraft for VS Code' }"
    >
      <p>The Kraft view lists your items in the board's groups. A gate opens as a rendered document, with Approve and Reject in the tab's title bar.</p>
      <p>Review a branch in the diff editor, file work from a Markdown file and see errors in Kraft's YAML as you type. It works in Cursor and VSCodium.</p>
      <template #picture>
        <SiteShot
          src="/site/vscode-gate.png"
          :width="1440"
          :height="640"
          alt="VS Code with the Kraft board in the sidebar and a gate's spec open as a Markdown preview, Approve and Reject in its title bar"
          :phone="{ src: '/site/vscode-gate-phone.png', width: 420, height: 184 }"
        />
        <figcaption>A gate's spec in a Markdown preview, with the board in the sidebar.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="console"
      picture="below"
      label="07 · CONSOLE"
      title="The board, in a terminal."
      :docs="{ to: '/reference/console', label: 'Console' }"
    >
      <p><code>kraft console</code> opens a terminal UI with Board, Server, Add-ons and Repos screens. It shows the web UI's statuses, words and colours.</p>
      <p>Peek at a spec or press <code>n</code> to file an item. It suits a tmux pane or SSH.</p>
      <template #picture>
        <SiteShot
          src="/site/console.png"
          :width="1482"
          :height="880"
          alt="kraft console in a terminal: the board's Needs you group, one item selected, and a peek at its spec below"
          :phone="{ src: '/site/console-phone.png', width: 440, height: 330 }"
        />
        <figcaption><code>kraft console</code> on the board's Needs you group, with a peek at the spec.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="agent-plugin"
      band
      label="08 · AGENT PLUGIN"
      title="Drive Kraft from your agent's session."
      :docs="{ to: '/guides/day-to-day/agent-integration', label: 'Use Kraft from your agent' }"
    >
      <p>The Kraft plugin gives Claude Code, Codex, Cursor and Antigravity slash commands and MCP tools. File work and clear gates without leaving the session.</p>
      <p>Ask "what is waiting on me in Kraft?" and the agent answers from the board. Other agents reach the same tools through the MCP server.</p>
      <template #picture>
        <dl class="s-ways">
          <div
            v-for="s in SKILLS"
            :key="s.name"
          >
            <dt><code>{{ s.name }}</code></dt>
            <dd>{{ s.text }}</dd>
          </div>
        </dl>
        <figcaption>Some of the plugin's commands.</figcaption>
      </template>
    </SiteBlock>

    <SiteInstall>
      <template #links>
        <SiteArrowLink to="/customize">
          Next: Make it yours
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
