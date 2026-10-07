<script>
// Nuxt UI's own prose table, copied (it is its template and two lines of
// setup) for one change: role="table" on the <table>. Its attrs go to the
// wrapping div, so a wrapper could not reach the element. A phone shows a
// table of three or more columns as blocks (assets/css/prose.css), and WebKit
// has dropped table semantics under a changed `display`; explicit roles on the
// table and on its rows and cells (ProseThead, ProseTbody, ProseTr, ProseTh,
// ProseTd) keep them. Redundant, and harmless, when it is a table.
import theme from "#build/ui/prose/table";
</script>

<script setup>
import { computed } from "vue";
import { useAppConfig } from "#imports";
import { useComponentProps } from "@nuxt/ui/composables/useComponentProps";
import { tv } from "@nuxt/ui/utils/tv";
const _props = defineProps({
  class: { type: null, required: false },
  ui: { type: Object, required: false }
});
defineSlots();
const props = useComponentProps("prose.table", _props);
const appConfig = useAppConfig();
const ui = computed(() => tv({ extend: theme, ...appConfig.ui?.prose?.table || {} })());
</script>

<template>
  <div :class="ui.root({ class: [props.ui?.root, props.class] })">
    <table role="table" :class="ui.base({ class: props.ui?.base })">
      <slot />
    </table>
  </div>
</template>
