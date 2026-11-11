<script setup lang="ts">
// Design K: one work item playing through Kraft's default chain by itself,
// stopping at its gates, so a newcomer sees what Kraft is in the first screen.
// The page renders in its idle state on the server, with the whole chain, every
// section and working links; the player (app/utils/homePlayer.ts) starts once
// the browser has mounted it. The copy is final.
const { v, start, approve, reject, toggle } = useHomePlayer()

// The demo is a folder of static files beside the pages, not a route: a plain
// link, with the base put on by hand.
const demo = `${useRuntimeConfig().app.baseURL.replace(/\/$/, '')}/demo/`
const repo = 'https://github.com/itsOmidKarami/kraft'
</script>

<template>
  <div class="home">
    <header class="k-header">
      <div class="k-wrap">
        <NuxtLink
          class="k-brand"
          to="/"
        >
          Kraft
        </NuxtLink>
        <NuxtLink to="/concepts/how-a-work-item-runs">
          How it works
        </NuxtLink>
        <NuxtLink to="/get-started/install">
          Docs
        </NuxtLink>
        <NuxtLink :to="repo">
          GitHub
        </NuxtLink>
      </div>
    </header>

    <main
      id="main-content"
      tabindex="-1"
    >
      <section class="k-wrap k-hero">
        <h1><span>Kraft runs the task.</span> <span>You make the calls.</span></h1>
        <p>Kraft is an app on your computer. It hands coding tasks to Claude Code, checks the work and stops only for the decisions that are yours. Watch one task run below, or press a button to make the call yourself.</p>
      </section>

      <section
        class="k-wrap"
        aria-label="A demo work item"
      >
        <div class="k-card">
          <div class="k-card-head">
            <span class="k-item">Cache the schema lookups</span>
            <span class="k-mono k-kind">work item · default chain</span>
            <span
              v-if="v.idle"
              class="k-badge k-badge-paused"
            >PAUSED</span>
            <span
              v-if="v.running"
              class="k-badge k-badge-running"
            ><span
              class="k-run-dot kr-run-dot"
              aria-hidden="true"
            />RUNNING</span>
            <span
              v-if="v.atGate"
              class="k-badge k-badge-needs"
            >NEEDS YOU</span>
            <span
              v-if="v.done"
              class="k-badge k-badge-done"
            >DONE</span>
          </div>

          <HomeTicks
            :nodes="v.nodes"
            :now="v.now"
          />
          <HomeChain :nodes="v.nodes" />

          <div
            class="kr-panel"
            aria-live="polite"
          >
            <template v-if="v.idle">
              <div class="k-col k-col-wide">
                <p class="k-big">
                  This work item is one coding task, filed with your spec.
                </p>
                <p class="k-sub">
                  It's paused and spends nothing until it starts. Dashed steps are still to run. Each diamond is a gate: a stop that waits for you.
                </p>
              </div>
              <button
                type="button"
                class="k-btn k-btn-primary"
                style="flex: 1 1 200px"
                @click="start"
              >
                Start the item
              </button>
            </template>

            <template v-else-if="v.running">
              <div class="k-col k-col-run">
                <p class="k-eyebrow">
                  {{ v.cap.actor }} · RUNNING
                </p>
                <p class="k-big">
                  {{ v.cap.title }}
                </p>
                <p
                  v-if="v.plainRun"
                  class="k-sub k-sub-run"
                >
                  {{ v.cap.detail }}
                </p>
                <p
                  v-else
                  class="k-sub k-sub-run"
                  style="min-height: 3em"
                >
                  {{ v.v.caption }}
                </p>
                <p
                  v-if="v.note"
                  class="k-note"
                ><span>Your note from {{ v.noteFrom }}</span><br>“{{ v.noteText }}” This step runs again with your note, then the gate asks again.</p>
              </div>
              <HomeVerify
                v-if="v.verifying"
                :v="v.v"
              />
            </template>

            <template v-else-if="v.atGate">
              <div class="k-col k-col-wide">
                <p class="k-eyebrow k-gate-eyebrow">
                  <span aria-hidden="true" />YOU · GATE
                </p>
                <p class="k-big">
                  {{ v.cap.title }}
                </p>
                <p class="k-sub">
                  {{ v.cap.detail }}
                </p>
                <p
                  v-if="v.autoHint"
                  class="k-hint"
                >
                  It will {{ v.autoVerb }} on its own in a moment, unless you press a button first.
                </p>
              </div>
              <div class="k-col k-col-act">
                <button
                  type="button"
                  class="k-btn"
                  :class="v.pressA ? 'k-btn-pressed-a kr-give' : 'k-btn-primary'"
                  @click="approve"
                >
                  Approve
                </button>
                <button
                  type="button"
                  class="k-btn"
                  :class="v.pressR ? 'k-btn-pressed-r kr-give' : 'k-btn-outline'"
                  @click="reject"
                >
                  Request changes
                </button>
                <div
                  v-if="v.counting"
                  class="k-count"
                  aria-hidden="true"
                >
                  <div class="kr-count" />
                </div>
              </div>
            </template>

            <template v-else-if="v.done">
              <HomeReceipt
                :log="v.log"
                :dec-word="v.decWord"
              />
              <div class="k-col k-col-act">
                <NuxtLink
                  class="k-btn k-btn-primary"
                  to="/get-started/install"
                >
                  Install Kraft
                </NuxtLink>
                <button
                  type="button"
                  class="k-btn k-btn-outline"
                  @click="start"
                >
                  Run it again
                </button>
              </div>
            </template>
          </div>

          <div class="k-card-foot">
            <span class="k-count-line">Decisions so far: <b>{{ v.decisions }}</b></span>
            <span class="kr-foot-note">Simplified and sped up: a real step takes minutes.</span>
            <button
              v-if="v.playing"
              type="button"
              class="k-toggle kr-touch"
              aria-label="Pause the demo"
              @click="toggle"
            >
              <svg
                width="10"
                height="10"
                viewBox="0 0 10 10"
                aria-hidden="true"
              ><rect
                x="1.5"
                y="1"
                width="2.5"
                height="8"
                rx="0.5"
                fill="currentColor"
              /><rect
                x="6"
                y="1"
                width="2.5"
                height="8"
                rx="0.5"
                fill="currentColor"
              /></svg>Pause
            </button>
            <button
              v-else
              type="button"
              class="k-toggle kr-touch"
              aria-label="Let the demo play on its own"
              @click="toggle"
            >
              <svg
                width="10"
                height="10"
                viewBox="0 0 10 10"
                aria-hidden="true"
              ><path
                d="M2 1.2v7.6a.5.5 0 0 0 .76.43l6.2-3.8a.5.5 0 0 0 0-.86l-6.2-3.8A.5.5 0 0 0 2 1.2z"
                fill="currentColor"
              /></svg>Play on its own
            </button>
          </div>
        </div>
      </section>

      <section
        class="k-wrap k-section"
        aria-labelledby="happened"
      >
        <h2
          id="happened"
          class="k-h2"
        >
          The same run, in the real app
        </h2>
        <div class="k-cols">
          <div>
            <h3>You hand it over</h3>
            <p>File a task from the board, the <code>kraft</code> command or your agent. Attach a spec or plan you've already settled, and the chain skips that step and its gate.</p>
          </div>
          <div>
            <h3>It runs without you</h3>
            <p>Each step runs in the item's own worktree, so your checkout stays as you left it. Caps on time, tokens and dollars stop a loop that won't converge, and you get its full trace.</p>
          </div>
          <div class="k-cols-warn">
            <h3>It finds you when it stops</h3>
            <p>A webhook or browser alert tells you, and the board lists what needs you first, on a laptop or a phone. Kraft refuses an agent that tries to decide its own gate.</p>
          </div>
        </div>
        <div class="k-shot">
          <LandingShot
            src="/assets/board.png"
            alt="The Kraft board: work items grouped by status with Needs you first, each with one button such as Review, Retry or Raise cap, and Running below"
            crop="210 0 1230 688"
            :width="1440"
          />
        </div>
        <p class="k-shot-note">
          The board: seven items waiting on a person, the rest running. Without a sandbox, an agent runs as you: <NuxtLink to="/project/security#agents-and-gates">what a gate holds back</NuxtLink>.
        </p>
      </section>

      <section
        class="k-wrap k-section k-start-section"
        aria-labelledby="start"
      >
        <div class="k-start-row">
          <div class="k-col k-col-wide">
            <h2 id="start">
              Now with your own repo.
            </h2>
            <p>You need macOS or Linux, git and a signed-in Claude Code. The shipped chain also needs a GitHub or GitLab remote. Open source, Apache-2.0.</p>
          </div>
          <div class="k-start-links">
            <NuxtLink
              class="k-btn k-btn-sm k-btn-primary"
              to="/get-started/install"
            >
              Install
            </NuxtLink>
            <a
              class="k-btn k-btn-sm k-btn-outline"
              :href="demo"
            >Try the full demo</a>
          </div>
        </div>
      </section>
    </main>

    <footer class="k-footer">
      <div class="k-wrap">
        <span class="k-source"><NuxtLink :to="repo">Source on GitHub</NuxtLink></span>
        <NuxtLink to="/get-started/why-kraft">
          Why Kraft
        </NuxtLink>
        <NuxtLink to="/get-started/faq">
          FAQ
        </NuxtLink>
      </div>
    </footer>
  </div>
</template>
