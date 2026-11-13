---
name: Kraft marketing site
description: The pages at the root of kraft-sdlc.dev that say what Kraft is, in dark and light.
colors:
  violet-field: "#231f52"
  violet-rule: "#38337a"
  on-violet-nav: "#dcdbf0"
  on-violet-lede: "#cfcde8"
  on-violet-caption: "#c2c0e6"
  on-violet-outline: "#7f7bcf"
  ground-dark: "#20202b"
  ground-light: "#f4f4fb"
  band-dark: "#15151f"
  band-light: "#e9e9f2"
  card-dark: "#2e2e39"
  card-light: "#ffffff"
  raised-dark: "#32323d"
  raised-light: "#eaeaf2"
  hairline-dark: "#393944"
  hairline-light: "#d5d5df"
  border-dark: "#51515d"
  border-light: "#b9b9c4"
  stroke-dark: "#777784"
  stroke-light: "#7a7a84"
  control-outline-dark: "#808090"
  control-outline-light: "#76767f"
  text-dark: "#eeeef2"
  text-light: "#15151a"
  text-sub-dark: "#c6c7cc"
  text-sub-light: "#35353b"
  text-muted-dark: "#9d9ea5"
  text-muted-light: "#5a5a63"
  glyph-todo-dark: "#767781"
  glyph-todo-light: "#81818a"
  accent-dark: "#b0adfc"
  accent-light: "#504a90"
  on-accent-dark: "#15151f"
  on-accent-light: "#ffffff"
  gate-amber-dark: "#deb866"
  gate-amber-light: "#875800"
  amber-wash-dark: "#332f31"
  amber-wash-light: "#efe2cd"
  amber-line-dark: "#292526"
  amber-line-light: "#e7dccd"
  running-blue-dark: "#80bdfc"
  running-blue-light: "#246099"
  blue-wash-dark: "#22293a"
  blue-wash-light: "#dde6f1"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "72px"
    fontWeight: 600
    lineHeight: 1.04
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "56px"
    fontWeight: 600
    lineHeight: 1.08
    letterSpacing: "-0.026em"
  block-head:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "40px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.022em"
  card-title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.018em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.012em"
  phone-head:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.024em"
  lede:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 400
    lineHeight: 1.55
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.6
  small:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "ui-monospace, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0.06em"
  label-small:
    fontFamily: "ui-monospace, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0.06em"
  code:
    fontFamily: "ui-monospace, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  mark: "3px"
  diamond: "6px"
  segment: "8px"
  control: "10px"
  panel: "12px"
  container: "16px"
spacing:
  gutter-phone: "24px"
  gutter-desktop: "96px"
  column: "1088px"
  block-gap: "72px"
  section-phone: "72px"
  section-desktop: "120px"
  control: "44px"
  control-large: "52px"
components:
  button-primary:
    backgroundColor: "{colors.accent-dark}"
    textColor: "{colors.on-accent-dark}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 26px"
    height: "52px"
  button-primary-small:
    backgroundColor: "{colors.accent-dark}"
    textColor: "{colors.on-accent-dark}"
    typography: "{typography.small}"
    rounded: "{rounded.control}"
    padding: "0 20px"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.card-dark}"
    textColor: "{colors.text-dark}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 26px"
    height: "52px"
  button-secondary-on-violet:
    textColor: "{colors.text-dark}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 26px"
    height: "52px"
  code-panel:
    backgroundColor: "{colors.band-dark}"
    textColor: "{colors.text-dark}"
    typography: "{typography.code}"
    rounded: "{rounded.panel}"
  drawing-panel:
    backgroundColor: "{colors.band-dark}"
    rounded: "{rounded.container}"
    padding: "28px"
  card:
    backgroundColor: "{colors.card-dark}"
    textColor: "{colors.text-dark}"
    rounded: "{rounded.container}"
    padding: "36px"
  product-menu:
    backgroundColor: "{colors.band-dark}"
    rounded: "{rounded.container}"
    padding: "12px"
    width: "560px"
  theme-switch:
    rounded: "{rounded.control}"
    padding: "3px"
    height: "44px"
---

# Design System: Kraft marketing site

<!-- Scope: the marketing pages in docsite/ (/, /how-it-works, /tour, /customize, /scale, /why). The docs pages keep Docus's look, and the web app's own system is frontend/src/ng/theme/theme.css. Source: the approved boards at https://claude.ai/artifact/1Cniz5ThNsGVeGSyWjFPjH and the Phase 5 build brief. -->

## Overview

**Creative North Star: "The Drafting Table"**

The site explains Kraft the way an engineer explains a machine: with a drawing first, its parts labelled, and a sentence or two beside it. Pages are airy, one idea per screen, set between playful and austere and leaning austere. Every drawing is built from the same few parts as the app itself: two glyph boxes (agent, Kraft), the gate diamond, 2px lines, dashed for what has not run yet, and monospace labels in capitals.

Colour is meaning, never ornament. A deep violet field opens and closes each page (the hero and the Install band). Between them the page steps through dark tonal bands. Amber appears only where a person decides, blue only where something runs at once or elsewhere, and violet marks links and the one primary action. A finished node is a solid outline, never green.

The interface appears as focused crops, never as a full window with its sidebar. There are no customer logos, testimonials, figures or benchmarks, and no icons beyond Kraft's own vocabulary.

The marketing pages' images live in `docsite/public/site/`, site code, not in `.github/assets/`: every version's build compiles these pages, and a release's own `.github/assets` replaces this checkout's for its docs. Five are copies of docs screenshots (`mobile.png`, `vscode-gate.png`, `console.png`, `chain-extends.png`, `harnesses.png`), so a retake of one is copied here too.

**Key Characteristics:**
- Drawings before screenshots; screenshots as tight crops.
- Two grounds alternate under every section; the violet field bookends the page.
- Amber means "you", blue means running; each sits beside a word.
- Inter for every heading and sentence; monospace only for labels, ids, paths and code.
- Rounded but never pill-shaped: 10px controls, 12px panels, 16px containers.

## Colors

A cool ink-and-violet ground with two signal colours that each carry one meaning.

### Primary
- **Deep Violet Field** (`violet-field`): the hero, the Install band, the frame behind screenshot crops and the header on those bands. Its hairline is **Violet Rule** (`violet-rule`). On the field, text keeps the dark theme's colours in both themes: headings in `text-dark`, header links in `on-violet-nav`, ledes in `on-violet-lede`, captions and fine print in `on-violet-caption`, and the secondary button's outline in `on-violet-outline`.
- **Violet Accent** (`accent-dark` / `accent-light`): links, the arrow links, and the primary button's fill, with `on-accent-*` text on it. Nothing else.

### Secondary
- **Gate Amber** (`gate-amber-*`): the gate diamond, the "YOU" lane label, the second line of the hero headline, "YOU" beside a gate in a node list, and `kind: gate` in a code panel. **Amber Wash** (`amber-wash-*`) fills the "You" lane and the gate panel; **Amber Line** (`amber-line-*`) highlights lines in a code panel.

### Tertiary
- **Running Blue** (`running-blue-*`): what runs at once or on another machine: the slice glyphs, the "3 SLICES" label, the other machine's lane label and glyph. **Blue Wash** (`blue-wash-*`) fills the other machine's lane.

### Neutral
- **Ground** (`ground-*`): the page's main band and the hero card's inside.
- **Band** (`band-*`): the alternate section band, the footer, code and drawing panels, the Product menu.
- **Card** (`card-*`): the two "Yours to shape and scale" cards, the secondary button's fill, the selected segment of the theme switch.
- **Raised** (`raised-*`): the inside of the current gate diamond.
- **Hairline** (`hairline-*`): rules between list items, panel headers and section edges.
- **Border** (`border-*`): outlines inside drawings (a scope box, a machine lane).
- **Stroke** (`stroke-*`): every line in a drawing and the dashed outline of a node still to come.
- **Control Outline** (`control-outline-*`): the secondary button's and the theme switch's outline. Darker than the boards' stroke so it reaches 3:1 on every ground it meets, the amber wash included.
- **Text** (`text-*`), **Secondary Text** (`text-sub-*`), **Muted Text** (`text-muted-*`): headings and current items; body copy and footer links; labels, captions and file paths.
- **Glyph To-do** (`glyph-todo-*`): the icon inside a node still to come. Decorative only, never text.

### Named Rules
**The One Meaning Rule.** Amber is "you", blue is "running at once or elsewhere", violet is "go here". A colour that means nothing in its spot is grey.

**The Word Beside Rule.** No colour carries meaning alone: amber sits beside "You" or a gate's question, blue beside "Another machine" or "3 slices".

**The Field Keeps Its Ink Rule.** On the violet field, text is the dark theme's in both themes.

## Typography

**Display Font:** Inter (with system-ui, sans-serif)
**Body Font:** Inter (with system-ui, sans-serif)
**Label/Mono Font:** ui-monospace (with Menlo, monospace)

**Character:** One quiet grotesque does all the talking, set tight at the top of the scale and loose in the body; the monospace is the drawing's lettering, used for labels and code and never for a sentence.

### Hierarchy
- **Display** (600, 72px, 1.04, -0.03em): the hero headline only, and "Not another coding agent." at 72px with 1.08.
- **Headline** (600, 56px, 1.08, -0.026em): section heads on Home, the How it works headline (1.06, -0.028em) and "Install Kraft.".
- **Block Head** (600, 40px, 1.1, -0.022em, balanced wrap): a capability block's claim.
- **Card Title** (600, 28px, 1.15, -0.018em): the two cards on Home.
- **Title** (600, 22px, 1.3, -0.012em): list heads under "How a run stays on track", the gate panel's question (1.25).
- **Lede** (400, 20px, 1.55): the sentence under a headline, max about 640px.
- **Body** (400, 17px, 1.6): paragraphs, buttons, nav links and arrow links (600 on links and primary buttons).
- **Small** (400, 15px, 1.5): captions, footer links, the hero diagram's step names.
- **Label** (600, 13px, 0.06em, capitals): a block's number and name ("01 · CHAINS"), a page's eyebrow, code panel headers (400).
- **Label Small** (500 or 600, 12px, 0.06em, capitals): footer column heads, "YOU", "AGENT", "KRAFT", drawing labels (0.5px tracking in SVG).
- **Phone Head** (600, 34px, 1.08 to 1.1, -0.024 to -0.028em): the hero and every section head on a phone; below it 22, 17, 15, 13 and 12.

### Named Rules
**The Lettering Rule.** Monospace is for labels, ids, file paths and code. A sentence in monospace is a bug.

**The Seventeen Rule.** Body text is 17px, and never under 15px at any width.

## Layout

The desktop design is 1280px wide with 96px side margins, a 1088px column. Sections are bands that run the full width, with 112px to 136px of vertical padding (the hero 72px top, 104px bottom). A capability block is a two-column grid, 5fr text to 7fr picture with a 72px gap, the sides alternating down the page; a block with a screenshot puts its text above and the crop across the full column. Section heads sit left with their arrow link on the right, bottom-aligned.

On a phone (designed at 390px) the side margins are 24px and sections 72px tall in padding (the hero 40px top, 56px bottom; the "Not another coding agent" band 80px). Everything stacks into one column; buttons go full width; the footer's link columns become a two-column grid under the wordmark and theme switch. The pages reflow from 320px with no sideways scroll, code included: a long line wraps in its own row, indented two characters past the line's start.

Spacing steps used: 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40, 48, 56, 64, 72.

## Elevation & Depth

Flat tonal layering. Depth comes from the bands stepping between ground, band and the violet field, and from hairlines. One object per page is lifted off the violet field: the hero card on Home and the video poster on How it works.

### Shadow Vocabulary
- **Lift on the field** (`box-shadow: 0 24px 48px rgba(8, 8, 30, 0.45)`): the hero card and the video poster, and the open Product menu. Nothing else.

### Named Rules
**The One Lifted Object Rule.** A page has one lifted object on its violet field, and the open Product menu floats while it is open. Everything else is flat.

## Shapes

Gently rounded and never pill-shaped. Controls take 10px, inner panels and list highlights 12px, containers and cards 16px. The theme switch's segments take 8px inside its 10px frame. The gate is a square turned 45 degrees with 6px corners; a node glyph box is 44px with 12px corners (36px with 10px, and 30px with 10px on a phone). Lines are 2px with round caps; a scope box or lane outline is 1px, dashed when the scope is a loop.

## Components

Quiet and exact: flat fills, hairlines and exact corners, no gloss and no motion that isn't the hero diagram's.

### Buttons
- **Shape:** gently rounded (10px).
- **Primary:** violet accent fill with on-accent text, 17px at 600, 52px tall with 26px sides; 44px tall with 20px sides and 15px text in the header.
- **Secondary:** a 1px control outline around a card fill (on a ground) or no fill (on the violet field, with the `on-violet-outline` outline), 17px at 500.
- **Arrow link:** accent text at 17px and 600 with a 16px arrow 8px after it, 44px tall.
- **Focus:** a 2px solid ring in the text colour with a 2px offset, on every control and link, in both themes and on the violet field.

### Cards / Containers
- **Corner Style:** 16px.
- **Background:** `card-*` with a hairline border, 36px padding (24px by 20px on a phone).
- **Shadow Strategy:** none (see Elevation).

### Navigation
- **Header:** 88px tall on the violet field with a violet-rule bottom edge: the wordmark (20px, 600), then Product (a disclosure), Why Kraft, Docs and GitHub at 17px and 500 in `on-violet-nav`, then a small primary Install button, 32px apart. On a phone, 64px tall: wordmark, Install and a 44px menu button.
- **Product menu:** a 560px two-column list on the band colour with a violet-rule border, 16px corners and 12px padding; each entry a 17px page name over a 15px line, 12px corners. Escape and a click outside close it, and focus returns to the button.
- **Phone menu:** a full-height sheet on the violet field: a "PRODUCT" label over the four entries (20px names with their lines), then Why Kraft, Docs and GitHub, with Install and Try the live demo pinned to the bottom.
- **Footer:** on the band colour with a hairline top: the wordmark and its meaning, the theme switch, and three link columns (Product, Docs, Project) with 12px monospace heads and 15px links, each 44px tall.
- **Theme switch:** a radio group labelled "Theme" with System, Light and Dark, 44px segments in a 10px control-outline frame; the checked segment takes the card fill and 600.

### Hero diagram (signature)
One work item on the default chain, drawn on the ground colour inside a 16px card: a header with the item's name and a monospace kind line, a pause button, then the "You" lane in amber wash above the steps with each gate's question over its diamond, the step glyphs on one line, and each step's actor and name below. Done steps are solid outlines, steps to come are dashed. Under it the gate panel, in amber wash, asks the current question with Approve and Request changes. On a phone the track stays one horizontal line and only the current step is spelled out, in a panel of fixed height below it.

### Code panel
A 12px band-coloured panel with a hairline: a monospace file path as its header, then the file at 13px with 1.6 line height. One line or group may be highlighted in amber line, with an amber diamond in the gutter beside a gate's id.

### Drawings
The fix loop, the scope nest, the worktrees, slices and the two machine lanes, each in a 16px band-coloured panel with 28px padding and a 15px muted caption below. All are SVG built from the two glyphs, the diamond, 2px strokes and 12px monospace labels, each with a text alternative that says what it shows.

### Install band
On the violet field: "Install Kraft." at 56px, then the command in a band-coloured 16px container with a Copy button that says it copied in words, then the next steps and requirements in on-violet text, then a row of arrow links.

## Do's and Don'ts

### Do:
- **Do** show the product as a drawing first, and the interface as a focused crop on the violet field.
- **Do** keep amber to the gates, the "You" lane and a decision waiting for a person, and blue to what runs at once or elsewhere.
- **Do** draw a finished node as a solid outline and a node still to come as dashed.
- **Do** use 10px corners on controls, 12px on inner panels and 16px on containers.
- **Do** keep every tap target at least 44px tall.

### Don't:
- **Don't** reuse the current home page's elements as they are.
- **Don't** use pill shapes or sharp boxes.
- **Don't** add icons beyond Kraft's own: the agent glyph, the Kraft glyph and the gate diamond.
- **Don't** turn a finished node green.
- **Don't** name an agent vendor in the hero; the line is "your coding agent".
- **Don't** invent customers, testimonials, numbers or benchmarks.
