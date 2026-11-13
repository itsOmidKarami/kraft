<!-- The hero's demo: one work item playing through the default chain, with the
     "You" lane above its steps and a panel for the current one below. The
     state machine is app/utils/homePlayer.ts; this is only its view. The track
     is drawn for the eye (aria-hidden); a screen reader gets the same chain as
     the list above it, each step with its state. While the demo plays on its
     own the panel is quiet; once the visitor takes over it is a live region.
     Focus on a button holds the run, and when a pressed button goes away
     focus moves to the panel, not to the page. While focus is inside the demo
     and the panel is quiet, reaching a gate is said politely, once.
     Under 720px of card the questions and names shrink and a key explains the
     glyphs; under 520px they give way to one line for the current step, and
     the panel keeps one height so the page does not jump. -->
<script setup lang="ts">
import { STEPS, TIMING } from '~/utils/homePlayer'
import type { PlayerNode } from '~/utils/homePlayer'

const card = useTemplateRef<HTMLElement>('card')
const panel = useTemplateRef<HTMLElement>('panel')
const { v, paused, start, approve, reject, toggle, hold } = useHomePlayer(card)

const act = (fn: () => void) => {
  fn()
  nextTick(() => {
    if (!document.activeElement || document.activeElement === document.body) panel.value?.focus()
  })
}

// The step names under the track, as the board words them.
const NAMES: Record<string, string> = {
  plan: 'Writes the plan',
  implementation: 'Writes the code',
  verification: 'Tests, then a code review',
  draft_merge_request: 'Opens a draft merge request',
  merge: 'Merges',
}

const state = (n: PlayerNode) => n.runDone || n.gateDone ? 'done' : n.runCur || n.gateCur ? 'cur' : 'todo'

const cols = computed(() => STEPS.map((s, i) => {
  const n = v.value.nodes[i]!
  return {
    gate: s.kind === 'gate',
    actor: s.actor,
    glyph: s.actor === 'AGENT' ? 'agent' as const : 'kraft' as const,
    name: s.kind === 'gate' ? s.title : NAMES[s.label]!,
    state: state(n),
    word: n.stateWord === 'not run yet' ? 'still to come' : n.stateWord,
    // The edge in from the step before, and out to the next.
    inDone: i > 0 && n.connDone,
    outDone: i < STEPS.length - 1 && v.value.nodes[i + 1]!.connDone,
    first: i === 0,
    last: i === STEPS.length - 1,
  }
}))

// On a phone-width card the names give way to one line for the current step.
const nowCol = computed(() => cols.value.find(c => c.state === 'cur'))

const cue = computed(() => v.value.autoVerb === 'approve'
  ? 'In this demo the gate approves itself after a few seconds. In Kraft it waits for you.'
  : 'In this demo, Request changes is pressed for you after a few seconds. In Kraft the gate waits for you.')

// A gate reached while focus is in the demo is said once. Only while the demo
// plays on its own: once the visitor drives, the panel says it as a live
// region. Read a moment later, after the player settles (under reduced motion
// it pauses at the gate, and then the demo does not decide it).
const inside = ref(false)
const said = ref('')
const leave = (e: FocusEvent) => {
  inside.value = !!card.value?.contains(e.relatedTarget as Node | null)
}
watch(
  () => [v.value.atGate ? `${v.value.cap.label}#${v.value.decisions}` : '', v.value.playing] as const,
  ([gate, playing], [oldGate, oldPlaying]) => {
    if (!gate) said.value = ''
    if (!gate || gate === oldGate || !(playing || oldPlaying) || !inside.value) return
    setTimeout(() => {
      if (!v.value.atGate) return
      said.value = `Gate: ${v.value.cap.title} ${v.value.counting ? cue.value : 'Waiting for you.'}`
    }, 0)
  },
)
</script>

<template>
  <div
    ref="card"
    class="s-demo s-themed"
    @focusin="inside = true"
    @focusout="leave"
  >
    <p
      class="s-sr"
      aria-live="polite"
    >
      {{ said }}
    </p>
    <div class="s-demo-head">
      <div class="s-demo-title">
        <span class="s-demo-item">Cache the schema lookups</span>
        <span class="s-demo-mono s-wide">work item · default chain · spec attached</span>
        <span class="s-demo-mono s-narrow"><template v-if="!v.idle && !v.done">step </template>{{ v.now.pos }} · {{ v.now.word }}</span>
      </div>
      <div class="s-demo-controls">
        <span class="s-demo-mono s-wide">{{ v.now.pos }} · {{ v.now.word }}</span>
        <button
          type="button"
          class="s-demo-pause"
          aria-label="Pause the demo"
          :aria-pressed="paused"
          @click="toggle"
        >
          <svg
            v-if="!paused"
            aria-hidden="true"
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="currentColor"
          ><path d="M4 3h3v10H4z" /><path d="M9 3h3v10H9z" /></svg>
          <svg
            v-else
            aria-hidden="true"
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="currentColor"
          ><path d="M4.5 2.5v11l9-5.5z" /></svg>
        </button>
      </div>
    </div>

    <ol
      class="s-sr"
      aria-label="The work item's chain"
    >
      <li
        v-for="c in cols"
        :key="c.name"
      >
        {{ c.gate ? `You: ${c.name}` : `${c.actor === 'AGENT' ? 'Agent' : 'Kraft'}: ${c.name}` }}, {{ c.word }}
      </li>
    </ol>

    <div
      class="s-track"
      aria-hidden="true"
    >
      <div class="s-lane">
        <div class="s-lane-label">
          YOU
        </div>
        <div class="s-cols">
          <div
            v-for="c in cols"
            :key="c.name"
            class="s-lane-cell"
          >
            <template v-if="c.gate">
              <div
                class="s-lane-question"
                :class="`is-${c.state}`"
              >
                {{ c.name }}
              </div>
              <div
                class="s-lane-gate"
                :class="`is-${c.state}`"
              >
                <span class="s-diamond"><i /></span><span class="s-drop" />
              </div>
            </template>
          </div>
        </div>
      </div>
      <div class="s-cols">
        <div
          v-for="c in cols"
          :key="c.name"
          class="s-node"
          :class="[`is-${c.state}`, { 'is-gate': c.gate }]"
        >
          <span
            v-if="!c.first"
            class="s-edge s-edge-in"
            :class="{ 'is-done': c.inDone }"
          />
          <span
            v-if="!c.last"
            class="s-edge s-edge-out"
            :class="{ 'is-done': c.outDone }"
          />
          <template v-if="c.gate">
            <span class="s-rise" /><span class="s-dot" />
          </template>
          <span
            v-else
            class="s-box"
          ><SiteGlyph
            :name="c.glyph"
            :size="20"
          /></span>
        </div>
      </div>
      <div class="s-cols s-names">
        <div
          v-for="c in cols"
          :key="c.name"
          :class="`is-${c.state}`"
        >
          <template v-if="!c.gate">
            <span class="s-names-actor">{{ c.actor }}</span>
            <span class="s-names-name">{{ c.name }}</span>
          </template>
        </div>
      </div>
      <div class="s-key">
        <span
          v-if="nowCol"
          class="s-key-now"
          :class="{ 'is-gate': nowCol.gate }"
        >{{ nowCol.gate ? 'YOU' : nowCol.actor }} · {{ nowCol.name }}</span>
        <span><SiteGlyph
          name="agent"
          :size="14"
        />AGENT</span>
        <span><SiteGlyph
          name="kraft"
          :size="14"
        />KRAFT</span>
      </div>
    </div>

    <div
      ref="panel"
      class="s-panel"
      tabindex="-1"
      :class="{ 'is-gate': v.atGate }"
      :aria-live="v.playing ? 'off' : 'polite'"
    >
      <div class="s-panel-text">
        <template v-if="v.idle">
          <p class="s-panel-label">
            PAUSED
          </p>
          <p class="s-panel-title">
            This work item is one coding task, filed with your spec.
          </p>
          <p class="s-panel-detail">
            It's paused and spends nothing until it starts. Dashed steps are still to run. Each diamond is a gate: a stop that waits for you.
          </p>
        </template>
        <template v-else-if="v.atGate">
          <p class="s-panel-label">
            YOU · GATE
          </p>
          <p class="s-panel-title">
            {{ v.cap.title }}
          </p>
          <p class="s-panel-detail">
            {{ v.cap.detail }}
          </p>
          <p
            v-if="v.counting"
            class="s-panel-auto"
            :style="{ '--s-gate-wait': `${TIMING.gateWait}ms` }"
          >
            {{ cue }}
            <span
              class="s-panel-auto-bar"
              aria-hidden="true"
            ><i /></span>
          </p>
        </template>
        <template v-else-if="v.running">
          <p class="s-panel-label">
            {{ v.cap.actor }} · RUNNING
          </p>
          <p class="s-panel-title">
            {{ v.cap.title }}
          </p>
          <p
            v-if="v.note"
            class="s-panel-detail"
          >
            Your note from {{ v.noteFrom }}: “{{ v.noteText }}” This step runs again with your note, then the gate asks again.
          </p>
          <p
            v-else
            class="s-panel-detail"
          >
            {{ v.verifying ? v.v.caption : v.cap.detail }}
          </p>
        </template>
        <template v-else>
          <p class="s-panel-label">
            MERGED
          </p>
          <p class="s-panel-title">
            All a person did: {{ v.decWord }} decisions.
          </p>
          <p class="s-panel-detail">
            Your coding agent wrote the plan and the code. Kraft ran the tests and reviews, opened the merge request and merged it.
          </p>
        </template>
      </div>
      <div
        v-if="v.atGate"
        class="s-panel-actions"
        @focusin="hold"
      >
        <button
          type="button"
          class="s-btn s-btn-primary"
          :class="{ 'is-pressed': v.pressA }"
          @click="act(approve)"
        >
          Approve
        </button>
        <button
          type="button"
          class="s-btn s-btn-secondary"
          :class="{ 'is-pressed': v.pressR }"
          @click="act(reject)"
        >
          Request changes
        </button>
      </div>
      <div
        v-else-if="v.idle || v.done"
        class="s-panel-actions"
        @focusin="hold"
      >
        <button
          type="button"
          class="s-btn s-btn-primary"
          @click="act(start)"
        >
          {{ v.idle ? 'Start the item' : 'Run it again' }}
        </button>
      </div>
    </div>
  </div>
</template>
