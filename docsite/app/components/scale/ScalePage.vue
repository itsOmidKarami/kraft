<!-- Scale up (docsite/DESIGN.md; the Bigger board for its two blocks, the
     page head and Install band from How it works). The blocks' copy is the
     approved board's; the head's lede is built from approved lines (Home's
     slices card, this page's remote-machines block). -->
<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)

// The head's picture: each strip is a chain's steps, `done`, `run`, `you` or
// `todo`; the word after it carries the meaning, so the strip is hidden from
// a screen reader.
const SLICES = [
  { name: 'SLICE 1', steps: ['done', 'done', 'done', 'run', 'todo'], state: 'RUNNING', tone: 'run' },
  { name: 'SLICE 2', steps: ['done', 'done', 'you', 'todo', 'todo'], state: 'NEEDS YOU', tone: 'you' },
  { name: 'SLICE 3', steps: ['done', 'run', 'todo', 'todo', 'todo'], state: 'RUNNING', tone: 'run' },
]
const CHAIN = ['done', 'done', 'moved', 'todo', 'todo']
</script>

<template>
  <div>
    <SitePageHead
      label="PRODUCT · SCALE UP"
      title="Run more than one thing at a time."
      top
    >
      Split a large plan into slices that several agents build at once, or move one step of a chain to another computer you already have.
      <template #picture>
        <figure class="s-figure">
          <div class="s-headpanel s-strips">
            <p class="s-strips-head">
              <span>SPLIT A PLAN</span>
              <span class="s-headpanel-note">3 SLICES OF ONE WORK ITEM</span>
            </p>
            <ul>
              <li
                v-for="s in SLICES"
                :key="s.name"
                class="s-strips-row"
              >
                <span class="s-strips-name">{{ s.name }}</span>
                <span
                  class="s-strip"
                  aria-hidden="true"
                ><i
                  v-for="(step, i) in s.steps"
                  :key="i"
                  :class="`is-${step}`"
                /></span>
                <b :class="`is-${s.tone}`">{{ s.state }}</b>
              </li>
            </ul>
            <hr>
            <p class="s-strips-head">
              <span>MOVE A STEP</span>
              <span class="s-headpanel-note">ONE STEP OF A CHAIN</span>
            </p>
            <div class="s-strips-row s-strips-move">
              <span class="s-strips-name">CHAIN</span>
              <span class="s-strips-lanes">
                <span
                  class="s-strip"
                  aria-hidden="true"
                ><i
                  v-for="(step, i) in CHAIN"
                  :key="i"
                  :class="`is-${step}`"
                /></span>
                <span class="s-strips-machine">
                  <span class="s-strips-where">ANOTHER MACHINE</span>
                  <span
                    class="s-strip"
                    aria-hidden="true"
                  ><i /><i /><i class="is-run" /><i /><i /></span>
                </span>
              </span>
              <b class="is-run">RUNNING</b>
            </div>
          </div>
          <figcaption>A slice that needs you doesn't hold up the others. One step of a chain can run on another machine.</figcaption>
        </figure>
      </template>
    </SitePageHead>

    <SiteBlock
      id="slices"
      label="01 · SLICES"
      title="Build a large plan in parallel."
      :docs="{ to: '/guides/day-to-day/split-work-into-slices', label: 'Split work into slices' }"
    >
      <p>Name the slices in the plan. Each one becomes a work item of its own, with its own worktree and review, and they run at once, up to the limit in your policy.</p>
      <p>When every slice has ended, Kraft joins the results. Verification, review and the merge request then run once, on the combined code.</p>
      <template #picture>
        <div class="s-drawing s-drawing-roomy">
          <SiteDrawingSlices
            class="s-draw-big"
            size="wide"
          />
          <SiteDrawingSlices
            class="s-draw-small"
            size="compact"
          />
        </div>
        <figcaption>One slice failing or needing you doesn't stop the others.</figcaption>
      </template>
    </SiteBlock>

    <SiteBlock
      id="remote-machines"
      band
      picture="left"
      label="02 · REMOTE MACHINES"
      title="Run a step on another machine."
      :docs="{ to: '/guides/run/run-a-step-on-another-machine', label: 'Run a step on another machine' }"
    >
      <p>Move one step of a chain to another computer you already have: a build box, a GPU machine, a Linux host.</p>
      <p>The board, the gates and every decision stay on your machine, and so do the pushes to your forge and its credentials. The other machine gets the commits and files the step needs, and sends back what it made.</p>
      <template #picture>
        <div class="s-drawing s-drawing-roomy">
          <SiteDrawingMachines />
        </div>
        <figcaption>The other machine never pushes or merges. Every commit and file that comes back is checked before the item's branch moves.</figcaption>
      </template>
    </SiteBlock>

    <SiteInstall>
      <template #links>
        <SiteArrowLink to="/why">
          Next: Why Kraft
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
