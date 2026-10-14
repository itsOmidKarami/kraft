<script setup lang="ts">
// A diagram of things and the edges between them, where a line of stages
// cannot say it: the status state machine, the parts of the process. Each
// node is listed once, with the edges that leave it, `label → target`, so a
// reader follows an edge by finding its target's name. Nodes may sit in
// groups (one process, one per running task). Used from Markdown as
// `::diagram-graph` with a YAML block, as DiagramFlow.vue shows.
export interface Edge {
  to: string
  label?: string
  dashed?: boolean
}

export interface GraphNode {
  name: string
  note?: string
  // A person decides here: the chip's words.
  gate?: string
  edges?: Edge[]
}

const props = defineProps<{
  caption: string
  nodes?: GraphNode[]
  groups?: { label: string, nodes: GraphNode[] }[]
}>()
</script>

<template>
  <DiagramFigure :caption="props.caption">
    <div class="diagram-graph">
      <template v-for="(group, i) in props.groups ?? [{ label: '', nodes: props.nodes ?? [] }]" :key="i">
        <!-- A group's frame spans the row and holds its nodes' own grid. -->
        <div :class="group.label ? 'diagram-group diagram-graph__group' : 'diagram-graph__group diagram-graph__group--bare'">
          <span v-if="group.label" class="diagram-group__label">{{ group.label }}</span>
          <ul class="diagram-graph__nodes">
            <li v-for="node in group.nodes" :key="node.name" class="diagram-graph__node">
              <span class="diagram-name" :class="{ 'diagram-gate': node.gate }">{{ node.name }}</span>
              <span v-if="node.gate" class="diagram-chip">[{{ node.gate }}]</span>
              <span v-if="node.note" class="diagram-note">{{ node.note }}</span>
              <ul v-if="node.edges?.length" class="diagram-graph__edges">
                <li v-for="(edge, j) in node.edges" :key="j">
                  <template v-if="edge.label"><span class="diagram-label">{{ edge.label }}<span class="sr-only">:</span></span>{{ ' ' }}</template>
                  <DiagramArrow :kind="edge.dashed ? 'dashed' : 'next'" />{{ ' ' }}<span class="diagram-name">{{ edge.to }}</span>
                </li>
              </ul>
            </li>
          </ul>
        </div>
      </template>
    </div>
  </DiagramFigure>
</template>

<style scoped>
.diagram-graph {
  display: grid;
  gap: 0.75rem;
}

/* Nodes side by side where two fit, one per row on a phone. */
.diagram-graph__nodes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  gap: 0.75rem 1.5rem;
}

.diagram-graph__node > .diagram-name {
  font-weight: 600;
}

/* An edge wraps under its label, not under the line's start. */
.diagram-graph__edges {
  margin-top: 0.125rem;
}

.diagram-graph__edges > li {
  padding-left: 4ch;
  text-indent: -2ch;
}
</style>
