<!-- Scale up (docsite/DESIGN.md; the Bigger board for its two blocks, the
     page head and Install band from How it works). The blocks' copy is the
     approved board's; the head's lede is built from approved lines (Home's
     slices card, this page's remote-machines block). -->
<script setup lang="ts">
const demo = demoHref(useRuntimeConfig().app.baseURL)
</script>

<template>
  <div>
    <SitePageHead
      label="PRODUCT · SCALE UP"
      title="Run more than one thing at a time."
    >
      Split a large plan into slices that several agents build at once, or move one step of a chain to another computer you already have.
    </SitePageHead>

    <SiteBlock
      id="slices"
      label="01 · SLICES"
      title="Build a large plan in parallel."
      :docs="{ to: '/guides/day-to-day/split-work-into-slices', label: 'Split work into slices' }"
    >
      <p>Name the slices in the plan. Each one becomes a work item of its own, with its own worktree and review, and they run at once.</p>
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
