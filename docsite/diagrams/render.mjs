// Render every diagram here twice, in the docs site's light and dark palettes:
// public/diagrams/<name>.svg and <name>.dark.svg. app/components/content/
// ProseImg.vue shows the one that matches the site's colour mode.
//
//   node diagrams/render.mjs [name ...]      # from docsite/; `just docs-diagrams`
//
// A diagram names its nodes' roles (`class api core`); the colours for each
// role are here, once per mode, so the .mmd files hold no colour at all.
// IBM Plex Sans, the site's own face, is embedded in each SVG: an <img> cannot
// load the page's fonts, and Mermaid sizes every box by the font it renders
// with. MMDC_PUPPETEER_CONFIG names a puppeteer config for mermaid-cli (a
// browser path, `--no-sandbox`) where its own Chromium cannot run.
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = join(here, '..', 'public', 'diagrams')
const tmp = mkdtempSync(join(tmpdir(), 'kraft-diagrams-'))

execFileSync('npm', ['pack', '--silent', '--pack-destination', tmp, '@fontsource/ibm-plex-sans@5.3.0'], { stdio: 'ignore' })
execFileSync('tar', ['xzf', join(tmp, 'fontsource-ibm-plex-sans-5.3.0.tgz'), '-C', tmp])
const fontCss = [400, 500].map((weight) => {
  const woff2 = readFileSync(join(tmp, 'package', 'files', `ibm-plex-sans-latin-${weight}-normal.woff2`))
  return `@font-face{font-family:"IBM Plex Sans";font-weight:${weight};src:url(data:font/woff2;base64,${woff2.toString('base64')}) format("woff2")}`
}).join('')

// Tailwind's violet and slate: the site's primary and neutral colours.
// core: Kraft's own parts. hero: what the reader should find first (a person's
// decision, the agent at work). worker: what a worker owns. store: data and
// states. client: what calls in. ext: tools outside Kraft.
const palettes = {
  light: {
    vars: {
      background: 'transparent', textColor: '#334155', lineColor: '#94a3b8',
      primaryColor: '#ffffff', primaryBorderColor: '#cbd5e1', primaryTextColor: '#1e293b',
      clusterBkg: '#f8fafc', clusterBorder: '#e2e8f0', titleColor: '#64748b', edgeLabelBackground: '#ffffff',
      // stateDiagram
      transitionColor: '#94a3b8', transitionLabelColor: '#475569', stateLabelColor: '#1e293b', specialStateColor: '#7c3aed',
      // sequenceDiagram
      actorBkg: '#f5f3ff', actorBorder: '#8b5cf6', actorTextColor: '#2e1065', actorLineColor: '#cbd5e1',
      signalColor: '#64748b', signalTextColor: '#1e293b', sequenceNumberColor: '#ffffff',
      noteBkgColor: '#f1f5f9', noteBorderColor: '#cbd5e1', noteTextColor: '#334155',
      activationBkgColor: '#ede9fe', activationBorderColor: '#a78bfa',
    },
    roles: {
      core: 'fill:#f5f3ff,stroke:#8b5cf6,color:#2e1065',
      hero: 'fill:#7c3aed,stroke:#6d28d9,color:#ffffff',
      worker: 'fill:#ede9fe,stroke:#a78bfa,color:#2e1065',
      store: 'fill:#f1f5f9,stroke:#94a3b8,color:#1e293b',
      client: 'fill:#ffffff,stroke:#cbd5e1,color:#1e293b',
      ext: 'fill:#ffffff,stroke:#94a3b8,color:#334155,stroke-dasharray:4 3',
    },
  },
  dark: {
    vars: {
      background: 'transparent', textColor: '#cbd5e1', lineColor: '#64748b',
      primaryColor: '#0f172a', primaryBorderColor: '#475569', primaryTextColor: '#e2e8f0',
      clusterBkg: '#1e293b66', clusterBorder: '#334155', titleColor: '#94a3b8', edgeLabelBackground: '#0f172a',
      transitionColor: '#64748b', transitionLabelColor: '#94a3b8', stateLabelColor: '#e2e8f0', specialStateColor: '#a78bfa',
      actorBkg: '#2e1065', actorBorder: '#a78bfa', actorTextColor: '#ede9fe', actorLineColor: '#334155',
      signalColor: '#94a3b8', signalTextColor: '#e2e8f0', sequenceNumberColor: '#ffffff',
      noteBkgColor: '#1e293b', noteBorderColor: '#475569', noteTextColor: '#cbd5e1',
      activationBkgColor: '#4c1d95', activationBorderColor: '#8b5cf6',
    },
    roles: {
      core: 'fill:#2e106599,stroke:#a78bfa,color:#ede9fe',
      hero: 'fill:#7c3aed,stroke:#a78bfa,color:#ffffff',
      worker: 'fill:#4c1d9566,stroke:#8b5cf6,color:#ede9fe',
      store: 'fill:#1e293b,stroke:#64748b,color:#e2e8f0',
      client: 'fill:#0f172a,stroke:#475569,color:#e2e8f0',
      ext: 'fill:#0f172a,stroke:#64748b,color:#cbd5e1,stroke-dasharray:4 3',
    },
  },
}

const themeCSS = `${fontCss}
.node rect, .node polygon, .basic.label-container { rx: 10px; ry: 10px; }
.cluster rect { rx: 14px; ry: 14px; }
.cluster-label text, .cluster-label .nodeLabel { font-weight: 500; letter-spacing: .02em; }
.edgeLabel, .edgeLabel text, .edgeLabel tspan { font-size: 13px; }
rect.actor { rx: 10px; ry: 10px; }
.sequenceNumber { font-weight: 500; }
@media (prefers-reduced-motion: reduce) {
  .edge-animation-fast, .edge-animation-slow { animation: none !important; stroke-dasharray: none !important; }
}`

const wanted = process.argv.slice(2)
const names = readdirSync(here).filter((f) => f.endsWith('.mmd')).map((f) => basename(f, '.mmd'))
  .filter((n) => !wanted.length || wanted.includes(n))
const pptr = process.env.MMDC_PUPPETEER_CONFIG
const pageCss = join(tmp, 'page.css')
writeFileSync(pageCss, fontCss)

for (const name of names) {
  const source = readFileSync(join(here, `${name}.mmd`), 'utf8')
  for (const [mode, { vars, roles }] of Object.entries(palettes)) {
    // classDef works in flowcharts and state diagrams; a sequence diagram has
    // no nodes to class, and takes its colours from the theme alone.
    const defs = source.startsWith('sequenceDiagram') ? '' : Object.entries(roles).map(([k, v]) => `  classDef ${k} ${v}`).join('\n')
    const mmd = join(tmp, `${name}.${mode}.mmd`)
    writeFileSync(mmd, `${source.trimEnd()}\n${defs}\n`)
    const config = join(tmp, `${mode}.json`)
    writeFileSync(config, JSON.stringify({
      theme: 'base', htmlLabels: false, themeCSS,
      flowchart: { htmlLabels: false, curve: 'basis', nodeSpacing: 28, rankSpacing: 44, padding: 14, wrappingWidth: 320 },
      state: { padding: 10 },
      sequence: { actorMargin: 40, boxMargin: 8, noteMargin: 10, messageMargin: 32, mirrorActors: false },
      themeVariables: { fontFamily: '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif', fontSize: '15px', ...vars },
    }))
    const target = join(out, mode === 'light' ? `${name}.svg` : `${name}.dark.svg`)
    execFileSync('npx', ['-y', '@mermaid-js/mermaid-cli@11', ...(pptr ? ['-p', pptr] : []),
      '-c', config, '-C', pageCss, '-b', 'transparent', '-i', mmd, '-o', target], { stdio: 'inherit' })
  }
}
