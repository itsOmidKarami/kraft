<script>
// Nuxt UI's own prose image, copied from @nuxt/ui 4.11.2
// (dist/runtime/components/prose/Img.vue) as ProseTable.vue copies the table:
// what it does is in its template and a few lines of setup, and its slots
// cannot take a different structure. On a Nuxt UI bump, diff this file against
// that one and re-copy it, then reapply the two changes below. What differs
// from the original, besides the imports (`@nuxt/ui/...` for its relative
// ones): the <DefineViewTemplate> wrapper around the DialogRoot, the dialog
// markup and close button, the `open` watcher, `landscapeDiagram`, the
// tabindex/role/keydown on the picture, and the wrapper and caption at the end
// of the template. The changes:
//
// 1. A tapped screenshot opened enlarged with nothing to close it but another
//    tap, no dialog role and no name. The layer is now role="dialog", named by
//    the image's alt text, with a visible close button that takes the focus;
//    Escape (Nuxt UI's own) and a tap anywhere close it as before, and the
//    focus goes back to the picture, which is a button now (tabindex, Enter
//    and Space open it). Tab stays on the close button: it is all there is.
// 2. A landscape diagram (public/diagrams/*.svg, wider than a phone) is wrapped
//    in .diagram-scroll, the box that scrolls sideways under 640px
//    (assets/css/prose.css), with a caption under it that says so. The caption
//    is for the eye (aria-hidden): the picture's alt text is what a screen
//    reader reads, once, and it cannot swipe. structure.svg and portrait/ fit
//    a phone and have neither.
import theme from "#build/ui/prose/img";
</script>

<script setup>
import { ref, computed, nextTick, useId, watch } from "vue";
import { DialogRoot, DialogPortal, DialogTrigger } from "reka-ui";
import { AnimatePresence, Motion } from "motion-v";
import { useEventListener, createReusableTemplate } from "@vueuse/core";
import { useRuntimeConfig, useAppConfig } from "#imports";
import ImageComponent from "#build/ui-image-component";
import { useComponentProps } from "@nuxt/ui/composables/useComponentProps";
import { resolveBaseURL } from "@nuxt/ui/utils";
import { tv } from "@nuxt/ui/utils/tv";
defineOptions({ inheritAttrs: false });
const _props = defineProps({
  src: { type: String, required: true },
  alt: { type: String, required: true },
  width: { type: [String, Number], required: false },
  height: { type: [String, Number], required: false },
  class: { type: null, required: false },
  zoom: { type: Boolean, required: false, default: true },
  ui: { type: Object, required: false }
});
const props = useComponentProps("prose.img", _props);
const appConfig = useAppConfig();
const [DefineImageTemplate, ReuseImageTemplate] = createReusableTemplate();
const [DefineZoomedImageTemplate, ReuseZoomedImageTemplate] = createReusableTemplate();
const [DefineViewTemplate, ReuseViewTemplate] = createReusableTemplate();
const open = ref(false);
const closeButton = ref(null);
const ui = computed(() => tv({ extend: theme, ...appConfig.ui?.prose?.img || {} })({
  zoom: props.zoom,
  open: open.value,
  width: !!props.width
}));
const refinedSrc = computed(() => resolveBaseURL(props.src, useRuntimeConfig().app.baseURL));
const layoutId = computed(() => `${refinedSrc.value}::${useId()}`);
const landscapeDiagram = computed(() => /\/diagrams\//.test(props.src) && !/\/diagrams\/(portrait\/|structure\.svg$)/.test(props.src));
if (props.zoom) {
  useEventListener(window, "scroll", () => {
    open.value = false;
  });
  useEventListener(window, "keydown", (e) => {
    if (e.key === "Escape" && open.value) {
      open.value = false;
    }
  });
}
let opener = null;
watch(open, (isOpen) => {
  if (isOpen) {
    opener = document.activeElement;
    nextTick(() => closeButton.value?.focus());
  } else {
    opener?.focus();
    opener = null;
  }
});
</script>

<template>
  <DefineImageTemplate>
    <component
      :is="ImageComponent"
      :src="refinedSrc"
      :alt="props.alt"
      :width="props.width"
      :height="props.height"
      v-bind="$attrs"
      :tabindex="props.zoom ? 0 : undefined"
      :role="props.zoom ? 'button' : undefined"
      :class="ui.base({ class: [props.ui?.base, props.class] })"
      @keydown.enter.space.prevent="open = true"
    />
  </DefineImageTemplate>

  <DefineZoomedImageTemplate>
    <component
      :is="ImageComponent"
      :src="refinedSrc"
      :alt="props.alt"
      v-bind="$attrs"
      :class="ui.zoomedImage({ class: [props.ui?.zoomedImage] })"
    />
  </DefineZoomedImageTemplate>

  <DefineViewTemplate>
    <DialogRoot v-if="props.zoom" v-slot="{ close }" v-model:open="open" :modal="false">
      <DialogTrigger as-child>
        <Motion :layout-id="layoutId" as-child :transition="{ type: 'spring', bounce: 0.15, duration: 0.5, ease: 'easeInOut' }">
          <ReuseImageTemplate />
        </Motion>
      </DialogTrigger>

      <DialogPortal>
        <AnimatePresence>
          <Motion v-if="open" :initial="{ opacity: 0 }" :animate="{ opacity: 1 }" :exit="{ opacity: 0 }" :class="ui.overlay({ class: [props.ui?.overlay] })" />

          <div
            v-if="open"
            role="dialog"
            aria-modal="true"
            :aria-label="props.alt"
            :class="ui.content({ class: [props.ui?.content] })"
            @click="close"
            @keydown.tab.prevent="closeButton?.focus()"
          >
            <button
              ref="closeButton"
              type="button"
              aria-label="Close"
              class="fixed top-3 right-3 z-10 inline-flex size-10 cursor-pointer items-center justify-center rounded-full bg-default text-highlighted shadow-md ring ring-default focus-visible:outline-2 focus-visible:outline-primary"
              @click.stop="close"
            >
              <UIcon name="i-lucide-x" class="size-5" aria-hidden="true" />
            </button>
            <Motion as-child :layout-id="layoutId" :transition="{ type: 'spring', bounce: 0.15, duration: 0.5, ease: 'easeInOut' }">
              <ReuseZoomedImageTemplate />
            </Motion>
          </div>
        </AnimatePresence>
      </DialogPortal>
    </DialogRoot>

    <ReuseImageTemplate v-else />
  </DefineViewTemplate>

  <template v-if="landscapeDiagram">
    <span class="diagram-scroll"><ReuseViewTemplate /></span>
    <span class="diagram-hint" aria-hidden="true">Swipe sideways to see all of it</span>
  </template>
  <ReuseViewTemplate v-else />
</template>
