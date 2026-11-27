<!-- The home page (docsite/DESIGN.md; the boards Main, Home-light and
     Home-phone). It makes the promise and routes onward: each section ends in
     a link to the page that says more. The copy is the approved boards'. -->
<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)

const DEFAULT_CHAIN = [
  '  - id: plan',
  '    kind: exec',
  '    tasks:',
  '      - id: author',
  '        extends: plan_author',
  ' ',
  '  - id: plan_approval',
  '    kind: gate',
  '    message: Review and approve the implementation plan.',
  '    artifact: plan',
  '    reject_to: plan',
  '    …',
]
const WITH_LINT = [
  'id: with-lint',
  'extends: default',
  ' ',
  'add:',
  '  - after: verification',
  '    node:',
  '      id: lint',
  '      extends: lint',
]
const RUN_POINTS = [
  { title: 'A process the agent can\'t skip.', text: 'Each work item follows a chain, a YAML file you can read and diff.' },
  { title: 'A person at the decisions.', text: 'A gate stops the chain until a decision is recorded. You approve, or request changes with a note.' },
  { title: 'Checks it doesn\'t grade itself on.', text: 'Kraft runs your tests in a node of its own, waits for your CI and has a separate reviewer decide whether a fix is done.' },
  { title: 'Capped retries and spend.', text: 'Attempts, time, tokens and dollars are bounded. A capped item stops and hands you the full trace.' },
]
</script>

<template>
  <div class="s-home">
    <section class="s-hero s-field">
      <div class="s-wrap s-hero-body">
        <div class="s-hero-text">
          <h1 class="s-display">
            <span>Kraft runs the task.</span>
            <span class="s-you">You make the calls.</span>
          </h1>
          <p class="s-lede">
            Kraft runs on your own machine. It hands coding tasks to your coding agent, checks the work and stops only for the decisions that are yours.
          </p>
          <div class="s-actions">
            <NuxtLink
              class="s-btn s-btn-primary"
              :to="INSTALL"
            >
              Install
            </NuxtLink>
            <a
              class="s-btn s-btn-secondary"
              :href="demo"
            >Try the live demo</a>
          </div>
        </div>
        <figure class="s-hero-figure">
          <HomeDemo />
          <figcaption>Watch one work item run, or press a button to make the call yourself.</figcaption>
        </figure>
      </div>
    </section>

    <section
      class="s-section s-band s-statement"
      aria-labelledby="not-another"
    >
      <div class="s-wrap">
        <h2
          id="not-another"
          class="s-h2 s-h2-big"
        >
          Not another coding agent.
        </h2>
        <p class="s-lede">
          Kraft runs the one you already use, and adds what a single session can't.
        </p>
        <SiteArrowLink to="/why">
          Why Kraft
        </SiteArrowLink>
      </div>
    </section>

    <section
      class="s-section"
      aria-labelledby="on-track"
    >
      <div class="s-wrap">
        <div class="s-head">
          <h2
            id="on-track"
            class="s-h2"
          >
            How a run stays on track.
          </h2>
          <SiteArrowLink to="/how-it-works">
            How it works
          </SiteArrowLink>
        </div>
        <div class="s-on-track">
          <ul class="s-points">
            <li
              v-for="p in RUN_POINTS"
              :key="p.title"
            >
              <h3>{{ p.title }}</h3>
              <p>{{ p.text }}</p>
            </li>
          </ul>
          <figure class="s-figure">
            <SiteCode
              path="chains/default.yaml"
              note="the default chain, as shipped"
              short="as shipped"
              :lines="DEFAULT_CHAIN"
              :hl="[6, 11]"
              :mark="6"
            />
            <figcaption>The highlighted node is a gate.</figcaption>
          </figure>
        </div>
      </div>
    </section>

    <section
      class="s-section s-band"
      aria-labelledby="board"
    >
      <div class="s-wrap">
        <div class="s-head">
          <div class="s-head-text">
            <h2
              id="board"
              class="s-h2"
            >
              One board for what needs you.
            </h2>
            <p class="s-lede">
              Open it on a laptop or a phone, or clear gates from VS Code or the command line.
            </p>
          </div>
          <SiteArrowLink to="/tour">
            Product tour
          </SiteArrowLink>
        </div>
        <figure class="s-figure">
          <div class="s-crop">
            <img
              class="s-wide"
              src="/site/needs-you.png"
              width="1180"
              height="216"
              alt="Three work items under Needs you on the Kraft board, each with the reason it stopped and one button"
              loading="lazy"
            >
            <img
              class="s-narrow"
              src="/site/needs-you-phone.png"
              width="374"
              height="228"
              alt="A work item under Needs you on the Kraft board, in the phone layout, waiting for a final review"
              loading="lazy"
            >
          </div>
          <figcaption>
            <span class="s-wide">Needs you, on the board: each item says why it stopped and offers one action.</span>
            <span class="s-narrow">Needs you, on the board's phone layout: the item says why it stopped and offers one action.</span>
          </figcaption>
        </figure>
      </div>
    </section>

    <section
      class="s-section"
      aria-labelledby="shape"
    >
      <div class="s-wrap">
        <h2
          id="shape"
          class="s-h2 s-head"
        >
          Yours to shape and scale.
        </h2>
        <div class="s-cards">
          <div class="s-card">
            <h3>Change the chain.</h3>
            <p>Extend the default chain or write your own. Share chains as add-ons. Pick the agent and model for each task.</p>
            <SiteCode
              class="s-card-picture"
              path="chains/with-lint.yaml"
              note="an example from the docs"
              short="from the docs"
              :lines="WITH_LINT"
              :hl="[1, 1]"
            />
            <SiteArrowLink to="/customize">
              Make it yours
            </SiteArrowLink>
          </div>
          <div class="s-card">
            <h3>Run more at once.</h3>
            <p>Split a large plan into slices that several agents build at once. Kraft joins the results before review.</p>
            <div class="s-drawing s-card-picture">
              <SiteDrawingSlices class="s-draw-big" />
              <SiteDrawingSlices
                class="s-draw-small"
                size="compact"
              />
            </div>
            <SiteArrowLink to="/scale">
              Scale up
            </SiteArrowLink>
          </div>
        </div>
      </div>
    </section>

    <SiteInstall>
      <p class="s-lede">
        Then start it with <code>kraft</code>, connect a repo with <code>kraft repo connect</code>, and file your first work item.
      </p>
      <p>You need macOS or Linux, git, uv and a signed-in Claude Code. Opening a merge request also needs gh or glab signed in, or an API token.</p>
      <p>Kraft is free and Apache-2.0. Your agent provider bills the sessions. It runs as one local process, with no telemetry.</p>
      <template #links>
        <SiteArrowLink :to="INSTALL">
          Install guide
        </SiteArrowLink>
        <SiteArrowLink to="/get-started/first-work-item">
          Your first work item
        </SiteArrowLink>
        <SiteArrowLink :href="demo">
          Try the live demo
        </SiteArrowLink>
        <SiteArrowLink :href="REPO">
          Source on GitHub
        </SiteArrowLink>
      </template>
    </SiteInstall>
  </div>
</template>
