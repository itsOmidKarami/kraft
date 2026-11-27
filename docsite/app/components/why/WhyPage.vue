<!-- Why Kraft (the Why board): what Kraft adds to the agent you already use,
     what else comes with it, how it compares and when not to use it. The copy
     is the approved board's; the docs keep the longer text. The old docs
     page's address and section anchors forward here (content/redirects.yml),
     and the comparison rows keep their ids. -->
<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)

const ADDS = [
  { title: 'A process the agent can\'t skip.', text: 'A chain is a file you can read and diff, from spec to merge.' },
  { title: 'Checks it doesn\'t grade itself on.', text: 'Kraft runs your tests and waits for your CI in nodes of their own.' },
  { title: 'A person at the decisions.', text: 'A gate stops the chain until a decision is recorded. Only a sandbox with a network policy keeps an agent from deciding its own gate, and none is set by default.' },
  { title: 'Capped retries and spend.', text: 'Time, token and dollar caps apply, and a defect the agent can\'t fix stops the item with the full trace.' },
]

const ALSO = [
  { label: 'LOCAL', title: 'It runs on your machine.', text: 'Kraft runs as one local process and binds loopback by default. It sends no telemetry. Your agent still talks to its own provider.', to: '/project/data-and-privacy', link: 'Docs: Data and privacy' },
  { label: 'CONTROLS', title: 'You set what an agent may do.', text: 'With Claude Code, Codex or Cursor, Kraft answers the agent\'s permission prompts from your policy and records each allow and deny. A sandbox can limit the hosts a task may reach.', to: '/project/security', link: 'Docs: Security' },
  { label: 'AGENTS AND MODELS', title: 'A different agent for each task.', text: 'Kraft ships seven harnesses, and each agent task names its own harness and model, so one can write the code and another review it. Every shipped task starts on Claude Code.', to: '/customize', link: 'Make it yours' },
  { label: 'REMOTE MACHINES', title: 'Run a step on another machine.', text: 'Move one step to a computer you already have: a build box, a GPU machine, a Linux host. The board, the gates and the pushes stay on yours.', to: '/scale#remote-machines', link: 'Scale up' },
  { label: 'SLICES', title: 'Build a large plan in parallel.', text: 'Split a large plan into slices that several agents build at once. Kraft joins the results before review.', to: '/scale#slices', link: 'Scale up' },
  { label: 'ONE BOARD', title: 'One board for every repo.', text: 'All your repos in one view, grouped by what needs you, what is running and what is done. A tunnel reaches it from a phone.', to: '/tour', link: 'Product tour' },
]

const NOT_FOR = [
  { title: 'A quick fix', text: 'Open a session. A chain is overhead you won\'t earn back.' },
  { title: 'Exploration', text: 'If you don\'t yet know what you want, you need a conversation, not a pipeline. Explore first, then hand off a spec.' },
  { title: 'Work you steer every few minutes', text: 'That is what a session is for.' },
  { title: 'A shared team service', text: 'Each Kraft instance is single-user and binds loopback by default. It isn\'t a hosted service.' },
]
</script>

<template>
  <div>
    <SitePageHead
      label="WHY KRAFT"
      title="Not another coding agent."
    >
      Kraft isn't a coding agent, and it doesn't replace yours. It runs the agent you already use, and adds what a single session can't give you.
      <template #picture>
        <figure class="s-figure">
          <div class="s-headpanel s-drawing-roomy">
            <SiteDrawingSession />
          </div>
          <figcaption>Close the terminal and a session stops. A work item runs unattended, and the board tells you when it needs you.</figcaption>
        </figure>
      </template>
    </SitePageHead>

    <section
      id="what-kraft-adds"
      class="s-section"
      aria-labelledby="what-kraft-adds-title"
    >
      <div class="s-wrap">
        <div class="s-head">
          <h2
            id="what-kraft-adds-title"
            class="s-block-title"
          >
            What Kraft adds.
          </h2>
          <SiteArrowLink to="/how-it-works">
            How it works
          </SiteArrowLink>
        </div>
        <ul class="s-why-adds">
          <li
            v-for="a in ADDS"
            :key="a.title"
          >
            <h3>{{ a.title }}</h3>
            <p>{{ a.text }}</p>
          </li>
        </ul>
      </div>
    </section>

    <section
      id="what-else-comes-with-it"
      class="s-section s-band"
      aria-labelledby="what-else-comes-with-it-title"
    >
      <div class="s-wrap">
        <h2
          id="what-else-comes-with-it-title"
          class="s-block-title s-why-h2"
        >
          What else comes with it.
        </h2>
        <ul class="s-why-also">
          <li
            v-for="a in ALSO"
            :key="a.label"
          >
            <p class="s-block-label">
              {{ a.label }}
            </p>
            <h3>{{ a.title }}</h3>
            <p>{{ a.text }}</p>
            <SiteArrowLink :to="a.to">
              {{ a.link }}
            </SiteArrowLink>
          </li>
        </ul>
      </div>
    </section>

    <section
      id="how-it-compares"
      class="s-section"
      aria-labelledby="how-it-compares-title"
    >
      <div class="s-wrap">
        <h2
          id="how-it-compares-title"
          class="s-block-title s-why-h2"
        >
          How it compares.
        </h2>
        <table
          class="s-compare"
          role="table"
        >
          <caption class="s-sr">
            Kraft compared with a loop, skills alone, Kraft Lite and CI
          </caption>
          <thead role="rowgroup">
            <tr role="row">
              <th
                scope="col"
                role="columnheader"
              >
                THE ALTERNATIVE
              </th>
              <th
                scope="col"
                role="columnheader"
              >
                ON ITS OWN
              </th>
              <th
                scope="col"
                role="columnheader"
                class="s-compare-kraft"
              >
                WITH KRAFT
              </th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            <tr
              id="a-loop"
              role="row"
            >
              <th
                scope="row"
                role="rowheader"
              >
                A loop
              </th>
              <td
                role="cell"
                data-label="ON ITS OWN">
                Runs an agent again and again until the agent decides it is done. Nothing bounds that decision, and a loop has no way to ask a person.
              </td>
              <td
                role="cell"
                data-label="WITH KRAFT">
                Retry loops stop at an attempt limit, a wall-clock limit or a token or dollar budget, and then hand the item to you.
              </td>
            </tr>
            <tr
              id="skills-and-plugins-alone"
              role="row"
            >
              <th
                scope="row"
                role="rowheader"
              >
                Skills and plugins alone
              </th>
              <td
                role="cell"
                data-label="ON ITS OWN">
                A skill tells an agent how to do one step well. The agent still decides whether to run it, in what order and when to stop, in one conversation that keeps growing.
              </td>
              <td
                role="cell"
                data-label="WITH KRAFT">
                The order, the gates and the retries are fixed by the file, and each agent task is its own session. Kraft runs your skills as tasks inside a chain.
              </td>
            </tr>
            <tr
              id="kraft-lite"
              role="row"
            >
              <th
                scope="row"
                role="rowheader"
              >
                Kraft Lite
              </th>
              <td
                role="cell"
                data-label="ON ITS OWN">
                Runs the same chain inside one agent session, for when you don't want a service. It keeps the fixed order and the gates.
              </td>
              <td
                role="cell"
                data-label="WITH KRAFT">
                Each agent task gets a fresh session. Lite runs every task in the one session you started.
              </td>
            </tr>
            <tr
              id="ci"
              role="row"
            >
              <th
                scope="row"
                role="rowheader"
              >
                CI
              </th>
              <td
                role="cell"
                data-label="ON ITS OWN">
                Runs a fixed script on a fresh runner.
              </td>
              <td
                role="cell"
                data-label="WITH KRAFT">
                Runs an agent on your machine that you can pause, steer or reject with a note. Kraft doesn't replace CI. It watches your merge request's pipeline and fixes what fails.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section
      id="when-not-to-use-kraft"
      class="s-section s-band"
      aria-labelledby="when-not-to-use-kraft-title"
    >
      <div class="s-wrap s-why-when">
        <div class="s-why-when-text">
          <h2
            id="when-not-to-use-kraft-title"
            class="s-block-title"
          >
            When not to use Kraft.
          </h2>
          <p>Use Kraft when you want agent work to run while you aren't watching, and to stop only where a person should decide. Use an interactive session for everything else.</p>
          <p>The usual pattern uses both: think in a session, settle a spec and a plan, hand them to Kraft, and take the next thing.</p>
        </div>
        <dl>
          <div
            v-for="n in NOT_FOR"
            :key="n.title"
          >
            <dt>{{ n.title }}</dt>
            <dd>{{ n.text }}</dd>
          </div>
          <div>
            <dt>A repo that isn't set up for it</dt>
            <dd>Kraft stops a repo's first item rather than guess its <code>test_command</code> or <code>setup_command</code>.</dd>
          </div>
        </dl>
      </div>
    </section>

    <SiteInstall>
      <template #links>
        <SiteArrowLink to="/get-started/faq">
          FAQ
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
