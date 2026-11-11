<script setup lang="ts">
import type { PlayerVerify } from '~/utils/homePlayer'

// The verification node opened: test_changed_scopes, then code_review, and the
// fix loop beneath them, repair then judge. The judge runs before repair, from
// the second repair on, so it never runs in this run. The wrapper carries the
// name the chain's verification box morphs into.
const props = defineProps<{ v: PlayerVerify }>()

type NodeState = 'idle' | 'run' | 'pass' | 'find'
type VNode = { name: string, icon: 'terminal' | 'agent', state: NodeState, words: Record<NodeState, string> }

const state = (run: boolean, pass: boolean, find: boolean): NodeState => run ? 'run' : pass ? 'pass' : find ? 'find' : 'idle'
const row = computed<VNode[]>(() => [
  { name: 'test_changed_scopes', icon: 'terminal', state: state(props.v.tRun, props.v.tPass, props.v.tFind), words: { idle: 'not run yet', run: 'running', pass: 'passed', find: '' } },
  { name: 'code_review', icon: 'agent', state: state(props.v.rRun, props.v.rPass, props.v.rFind), words: { idle: 'not run yet', run: 'running', pass: 'no findings', find: '1 finding' } },
])
const repair = computed<VNode>(() => ({ name: 'repair', icon: 'agent', state: state(props.v.pRun, props.v.pPass, props.v.pFind), words: { idle: 'not run yet', run: 'running', pass: 'committed', find: '' } }))
const judge: VNode = { name: 'judge', icon: 'agent', state: 'idle', words: { idle: 'from round 2', run: '', pass: '', find: '' } }

// A name breaks after an underscore, as the chain's labels do.
const parts = (name: string) => name.split(/(?<=_)/)
</script>

<template>
  <div
    class="k-canvas k-dots"
    style="view-transition-name: kr-verify"
  >
    <p class="k-canvas-title">
      <HomeIcon
        name="layers"
        :size="15"
      />
      <b>verification</b>
      <span>inside the node · round {{ v.round }}</span>
    </p>
    <div
      class="k-loop"
      :class="{ 'k-loop-on': v.loopOn }"
    >
      <span
        class="k-loop-arc"
        aria-hidden="true"
      />
      <svg
        class="k-loop-arrow"
        aria-hidden="true"
        width="12"
        height="8"
        viewBox="0 0 12 8"
      ><path
        d="M1 7l5-5 5 5"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
      /></svg>
      <div class="k-loop-row">
        <span
          class="k-start k-loop-begin"
          aria-hidden="true"
        >
          <span>
            <svg
              width="8"
              height="8"
              viewBox="0 0 10 10"
            ><path
              d="M3 1.5v7l5.5-3.5z"
              fill="currentColor"
            /></svg>
          </span>
        </span>
        <template
          v-for="(n, i) in row"
          :key="n.name"
        >
          <span
            class="k-loop-line"
            aria-hidden="true"
          ><i /></span>
          <div class="k-vnode k-vnode-row">
            <span
              class="k-box"
              :class="[`k-box-${n.state === 'idle' ? 'todo' : n.state === 'run' ? 'cur' : n.state === 'find' ? 'find' : 'done'}`, { 'kr-trace': n.state === 'run' }]"
            ><HomeIcon :name="n.icon" /></span>
            <span
              class="k-vname"
              :class="`k-vname-${n.state}`"
            ><template
              v-for="(part, j) in parts(n.name)"
              :key="j"
            ><wbr v-if="j">{{ part }}</template></span>
            <span
              class="k-vword"
              :class="`k-vword-${n.state}`"
            >{{ n.words[n.state] }}</span>
          </div>
          <span
            v-if="i === row.length - 1"
            class="k-loop-line"
            aria-hidden="true"
          ><i /></span>
        </template>
        <span
          class="k-loop-end"
          aria-hidden="true"
        >
          <i :class="{ 'k-end-fail': v.endFail, 'k-end-pass': v.endPass }" />
        </span>
      </div>
      <!-- The fix loop: judge before repair. -->
      <div
        v-for="(n, i) in [judge, repair]"
        :key="n.name"
        class="k-vnode-low"
        :style="{ left: i === 0 ? '67%' : '33%' }"
      >
        <span class="k-vnode">
          <span
            class="k-box"
            :class="[`k-box-${n.state === 'idle' ? 'todo' : n.state === 'run' ? 'cur' : 'done'}`, { 'kr-trace': n.state === 'run' }]"
          ><HomeIcon :name="n.icon" /></span>
          <span
            class="k-vname"
            :class="`k-vname-${n.state}`"
          >{{ n.name }}</span>
          <span
            class="k-vword"
            :class="`k-vword-${n.state}`"
          >{{ n.words[n.state] }}</span>
        </span>
      </div>
    </div>
  </div>
</template>
