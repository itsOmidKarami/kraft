<!-- Make it yours, block 01: the Chains screen editing `with-lint` beside the
     YAML file it writes, in two edits, then default changing underneath it,
     then a review. The frames and the view
     computation are app/utils/customizePlayer.ts; this is only its view. The
     app replica is drawn for the eye (aria-hidden) in the app's dark look on
     the violet frame; a screen reader gets the file and the caption, which is
     live only while the demo is paused. From 1100px the crop and the file sit
     side by side; under it a phone layout draws the chain's track above the
     file. Both read one player. A hovering mouse holds the run, and so does
     keyboard focus inside it, except on its own buttons. -->
<script setup lang="ts">
import { CAPS, PHONE_CAPS, STEPS, desktopView, phoneFrame, phoneView, stepOf } from '~/utils/customizePlayer'
import type { YamlLine } from '~/utils/customizePlayer'

const fig = useTemplateRef<HTMLElement>('fig')
const { state, toggle, go, enter, leave, focusin, focusout } = useCustomizePlayer(fig)

const f = computed(() => state.value.frame)
const playing = computed(() => state.value.playing)
const live = computed(() => (playing.value ? 'off' : 'polite'))
const step = computed(() => stepOf(f.value))
const d = computed(() => desktopView(f.value))
const pf = computed(() => phoneFrame(f.value))
const p = computed(() => phoneView(pf.value))

// A line not yet written keeps its place in the page, hidden.
const lineStyle = (l: YamlLine) => ({
  gridTemplateRows: l.shown ? '1fr' : '0fr',
  opacity: l.shown ? 1 : 0,
  visibility: l.shown ? 'visible' : 'hidden',
  paddingBlock: l.shown ? '1.5px' : '0',
  backgroundColor: l.lit ? '#292526' : 'transparent',
} as const)
// A wrapped line hangs under its text, not under its dash.
const textStyle = (l: YamlLine) => ({ paddingLeft: `${l.pad}ch` })
</script>

<template>
  <figure
    ref="fig"
    class="s-cdemo"
    aria-label="The Chains screen editing with-lint, beside the YAML file it writes"
    @pointerenter="enter"
    @pointerleave="leave"
    @focusin="focusin"
    @focusout="focusout"
  >
    <!-- Desktop: a 600px crop of the Chains canvas, then the file. -->
    <div class="s-cdemo-wide">
      <div class="s-cdemo-frame">
        <div
          class="s-cdemo-app s-cdemo-crop"
          aria-hidden="true"
        >
          <div
            class="s-cdemo-dots s-cdemo-canvas"
            :class="{ 'is-reset': d.reset }"
            :style="d.cv.style"
          >
            <div
              class="s-cdemo-view"
              :class="{ 'is-off': !d.cv.chain }"
            >
              <div class="s-cdemo-fade">
                <div
                  v-for="(e, i) in d.edges"
                  :key="`e${i}`"
                  class="s-cdemo-edge"
                  :style="e.style"
                />
                <div
                  v-for="(s, i) in d.seams"
                  :key="`s${i}`"
                  class="s-cdemo-seam"
                  :style="s.style"
                />
              </div>
              <div
                v-for="n in d.nodes"
                :key="n.id"
                class="s-cdemo-gnode"
                :style="n.wrap"
              >
                <span
                  v-if="n.isGate"
                  class="s-cdemo-gate"
                  :class="{ 'is-sel': n.sel }"
                />
                <div
                  v-else
                  class="s-cdemo-gbox"
                  :class="{ 'is-sel': n.sel }"
                >
                  <CustomizeGlyph :name="n.agent ? 'agent' : n.layers ? 'layers' : 'term'" />
                </div>
                <span
                  class="s-cdemo-glabel"
                  :class="{ 'is-bold': n.sel }"
                >{{ n.id }}</span>
                <span
                  class="s-cdemo-gmeta"
                  :style="n.metaStyle"
                >{{ n.meta }}</span>
              </div>
              <div
                class="s-cdemo-plus"
                :style="d.plusStyle"
              >+</div>
            </div>

            <div
              class="s-cdemo-view"
              :class="{ 'is-off': d.cv.chain }"
            >
              <p
                v-if="d.cv.banner"
                class="s-cdemo-banner"
              >Inherited from default: change it in YAML, or detach the chain.</p>
              <div
                class="s-cdemo-edge"
                :style="d.nv.e1"
              />
              <div
                class="s-cdemo-edge"
                :style="d.nv.e2"
              />
              <svg
                class="s-cdemo-mark"
                :style="d.nv.start"
                viewBox="0 0 16 16"
              ><circle
                cx="8"
                cy="8"
                r="7"
                fill="none"
                stroke="#c6c7cc"
                stroke-width="1.3"
              /><path
                d="M6.3 5v6l4.7-3z"
                fill="#c6c7cc"
              /></svg>
              <svg
                class="s-cdemo-mark"
                :style="d.nv.end"
                viewBox="0 0 16 16"
              ><rect
                x="1.5"
                y="1.5"
                width="13"
                height="13"
                rx="3.5"
                fill="#20202b"
                stroke="#777784"
                stroke-width="1.3"
              /></svg>
              <div
                v-if="d.nv.hasTask"
                class="s-cdemo-gnode"
                :style="d.nv.task"
              >
                <div
                  class="s-cdemo-gbox"
                  :class="{ 'is-sel': d.nv.sel }"
                >
                  <CustomizeGlyph name="agent" />
                </div>
                <span
                  class="s-cdemo-glabel"
                  :class="{ 'is-bold': d.nv.sel }"
                >implement</span>
              </div>
              <div
                v-if="d.nv.addTask"
                class="s-cdemo-plus"
                :style="d.nv.plus"
              >+</div>
              <div class="s-cdemo-back">
                <span class="s-cdemo-btn">← chain</span>
                <span class="s-cdemo-m s-cdemo-nodename">{{ d.cv.nodeName }}</span>
              </div>
            </div>

            <Transition name="s-cdemo-pop">
            <div
              v-if="d.menu.show"
              :key="d.menu.head || d.menu.items[0]?.title"
              class="s-cdemo-menu"
              :style="d.menu.style"
            >
              <div
                v-if="d.menu.head"
                class="s-cdemo-mhead"
              >{{ d.menu.head }}</div>
              <div
                v-if="d.menu.search"
                class="s-cdemo-search s-cdemo-m"
              >{{ d.menu.query }}</div>
              <div
                v-for="it in d.menu.items"
                :key="it.title"
                class="s-cdemo-mitem"
                :class="{ 'is-on': it.cls === 'on' }"
              >
                <CustomizeGlyph
                  :name="it.exec ? 'exec' : it.gate ? 'gate' : it.term ? 'term' : 'sparkle'"
                  class="s-cdemo-ic s-cdemo-mic"
                />
                <span class="s-cdemo-mtext"><span :class="{ 's-cdemo-m': it.titleCls }">{{ it.title }}</span><span class="s-cdemo-msub">{{ it.sub }}</span></span>
              </div>
            </div>
            </Transition>

            <Transition name="s-cdemo-pop">
            <div
              v-if="d.card.show"
              :key="d.card.title"
              class="s-cdemo-card"
              :style="d.card.style"
            >
              <p class="s-cdemo-m s-cdemo-crumbs">{{ d.card.crumbs }}</p>
              <p class="s-cdemo-m s-cdemo-ctitle">{{ d.card.title }}</p>
              <p class="s-cdemo-csub">{{ d.card.sub }}</p>
              <p
                v-if="d.card.note"
                class="s-cdemo-cnote"
              >{{ d.card.note }}</p>
              <div
                v-if="d.card.rows.length"
                class="s-cdemo-rows"
              >
                <template
                  v-for="r in d.card.rows"
                  :key="r.k"
                >
                  <div
                    v-if="r.isKv"
                    class="s-cdemo-prow"
                  >
                    <span :style="r.kStyle">{{ r.k }}</span>
                    <span :style="r.vStyle">{{ r.v }}</span>
                    <span
                      v-if="r.chip"
                      class="s-cdemo-chipsrc"
                      :style="r.chipStyle"
                    ><i
                      v-if="r.dot"
                      class="s-cdemo-dot"
                    />{{ r.chipText }}</span>
                  </div>
                  <div
                    v-else-if="r.isText"
                    class="s-cdemo-trow"
                  >
                    <span class="s-cdemo-tlabel">{{ r.k }}</span>
                    <span class="s-cdemo-tbox">{{ r.v }}</span>
                  </div>
                  <div
                    v-else
                    class="s-cdemo-change"
                  >
                    <span
                      class="s-cdemo-m"
                      :style="r.signStyle"
                    >{{ r.signText }}</span>
                    <span class="s-cdemo-changetext"><span class="s-cdemo-m s-cdemo-changek">{{ r.k }}</span><span class="s-cdemo-muted">{{ r.v }}</span></span>
                  </div>
                </template>
              </div>
              <div
                v-if="d.card.foot"
                class="s-cdemo-foot"
              >
                <span class="s-cdemo-btn s-cdemo-danger">Discard draft</span>
                <span class="s-cdemo-btn s-cdemo-pri">Publish</span>
              </div>
            </div>
            </Transition>
          </div>

          <div
            v-if="d.bp.show"
            class="s-cdemo-bp"
          >
            <div class="s-cdemo-tabs">
              <span class="s-cdemo-tab is-on"><CustomizeGlyph
                name="bolt"
                class="s-cdemo-ic s-cdemo-tabic"
              />On failure</span>
              <template v-if="d.bp.nodeTabs">
                <span class="s-cdemo-tab"><CustomizeGlyph
                  name="loop"
                  class="s-cdemo-ic s-cdemo-tabic"
                />Fix loop</span>
                <span class="s-cdemo-tab">Escalation</span>
              </template>
            </div>
          </div>
        </div>

        <div class="s-cdemo-file">
          <p class="s-cdemo-filehead">
            <Transition
              name="s-cdemo-swap"
              mode="out-in"
            ><span :key="d.path">{{ d.path }}</span></Transition>
            <span
              class="s-cdemo-note"
              :class="{ 'is-draft': d.changes }"
            >{{ d.note }}</span>
          </p>
          <Transition
            name="s-cdemo-swap"
            mode="out-in"
          >
          <div
            :key="d.path"
            class="s-cdemo-lines"
          >
            <div
              v-for="(l, i) in d.lines"
              :key="i"
              class="s-cdemo-yl"
              :class="{ 'is-hidden': !l.shown }"
              :style="lineStyle(l)"
            >
              <span
                class="s-cdemo-yt"
                :style="textStyle(l)"
              ><span>{{ l.prefix }}</span><span class="s-cdemo-yk">{{ l.key }}</span><span>{{ l.rest }}</span></span>
            </div>
          </div>
          </Transition>
        </div>
      </div>

      <div class="s-cdemo-bar">
        <div
          class="s-cdemo-caption"
          :aria-live="live"
        >
          <p class="s-cdemo-label">{{ CAPS[f]!.label }}</p>
          <p class="s-cdemo-title">{{ CAPS[f]!.title }}</p>
          <p class="s-cdemo-detail">{{ CAPS[f]!.detail }}</p>
        </div>
        <div class="s-cdemo-controls">
          <div
            class="s-cdemo-steps"
            role="group"
            aria-label="Jump to an edit"
          >
            <button
              v-for="(s, i) in STEPS"
              :key="s.name"
              type="button"
              class="s-cdemo-stepbtn"
              :class="{ 'is-on': step === i }"
              :aria-pressed="step === i"
              @click="go(i)"
            >{{ s.name }}</button>
          </div>
          <button
            type="button"
            class="s-cdemo-pause"
            aria-label="Pause the demo"
            :aria-pressed="!playing"
            @click="toggle"
          >
            <svg
              v-if="playing"
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
    </div>

    <!-- Phone: the caption and buttons, then the chain's track above the file; no
         inspector, no menus. The file grows below the controls, so they never move. -->
    <div class="s-cdemo-narrow">
      <div class="s-cdemo-pbar">
        <div
          class="s-cdemo-caption s-cdemo-pcaption"
          :aria-live="live"
        >
          <div class="s-cdemo-phead">
            <div class="s-cdemo-ptext">
              <p class="s-cdemo-label">{{ PHONE_CAPS[pf]!.label }}</p>
              <p class="s-cdemo-title">{{ PHONE_CAPS[pf]!.title }}</p>
            </div>
            <button
              type="button"
              class="s-cdemo-pause"
              aria-label="Pause the demo"
              :aria-pressed="!playing"
              @click="toggle"
            >
              <svg
                v-if="playing"
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
          <p class="s-cdemo-detail">{{ PHONE_CAPS[pf]!.detail }}</p>
        </div>
        <div
          class="s-cdemo-psteps"
          role="group"
          aria-label="Jump to an edit"
        >
          <button
            v-for="(s, i) in STEPS"
            :key="s.name"
            type="button"
            class="s-cdemo-stepbtn"
            :class="{ 'is-on': step === i }"
            :aria-label="s.name"
            :aria-pressed="step === i"
            @click="go(i)"
          >{{ s.short }}</button>
        </div>
      </div>
      <div class="s-cdemo-frame">
        <div
          class="s-cdemo-app s-cdemo-track"
          aria-hidden="true"
        >
          <div class="s-cdemo-trackbar">
            <span class="s-cdemo-m s-cdemo-trackname">with-lint ⌄</span>
            <span class="s-cdemo-extends">extends default</span>
            <span
              class="s-cdemo-state"
              :class="{ 'is-draft': p.draft }"
            >{{ p.state }}</span>
          </div>
          <div class="s-cdemo-lane">
            <div class="s-cdemo-pfade">
              <div
                v-for="(e, i) in p.edges"
                :key="i"
                class="s-cdemo-edge s-cdemo-pedge"
                :style="e.style"
              />
            </div>
            <div
              v-for="n in p.nodes"
              :key="n.id"
              class="s-cdemo-gnode s-cdemo-pnode"
              :style="n.wrap"
            >
              <div class="s-cdemo-gbox">
                <CustomizeGlyph :name="n.agent ? 'agent' : n.layers ? 'layers' : 'term'" />
              </div>
              <span
                class="s-cdemo-glabel"
                :class="n.below ? 's-cdemo-below' : 's-cdemo-above'"
              >{{ n.id }}</span>
              <span
                class="s-cdemo-gmeta s-cdemo-pmeta"
                :style="n.metaStyle"
              >{{ n.meta }}</span>
            </div>
          </div>
        </div>

        <div class="s-cdemo-file s-cdemo-pfile">
          <p class="s-cdemo-filehead"><span>{{ p.path }}</span></p>
          <div class="s-cdemo-lines s-cdemo-stack">
            <div
              v-for="(file, k) in [p.lines, p.defaultLines]"
              :key="k"
              class="s-cdemo-layer"
              :class="{ 'is-off': p.upstream !== (k === 1) }"
              :aria-hidden="p.upstream !== (k === 1)"
            >
              <div
                v-for="(l, i) in file"
                :key="i"
                class="s-cdemo-yl"
                :class="{ 'is-hidden': !l.shown }"
                :style="lineStyle(l)"
              >
                <span
                  class="s-cdemo-yt"
                  :style="textStyle(l)"
                ><span>{{ l.prefix }}</span><span class="s-cdemo-yk">{{ l.key }}</span><span>{{ l.rest }}</span></span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <figcaption class="s-cdemo-figcap">
      The Chains screen and the file it writes. The <code>lint</code> task is the one the guide adds to your library.
    </figcaption>
  </figure>
</template>
