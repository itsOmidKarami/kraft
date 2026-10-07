export default defineAppConfig({
  seo: {
    title: 'Kraft',
    description: 'A local orchestrator that takes your coding agent from spec to pull request, stopping only when a decision is yours.',
  },
  header: {
    title: 'Kraft',
  },
  // No `socials.github`: the footer already draws one GitHub icon from
  // `github.url`, and a second entry drew it twice.
  github: {
    owner: 'itsOmidKarami',
    name: 'kraft',
    url: 'https://github.com/itsOmidKarami/kraft',
    branch: 'main',
    rootDir: 'docsite',
  },
  ui: {
    colors: {
      primary: 'violet',
      neutral: 'slate',
    },
    // Nuxt UI cuts a long sidebar label short ("Add a security review or a
    // g…"). Wrap it instead, so the label can be the page's whole title.
    contentNavigation: {
      slots: {
        linkTitle: 'whitespace-normal!',
      },
    },
    // Nuxt UI cuts a long "On this page" entry short too, and three entries
    // that differ only after the cut read the same. Wrap it. whitespace-normal
    // only breaks at spaces, so one long identifier
    // (work_item_changed_test_selection) was still cut at the box's edge;
    // break-words lets it break there too. Not wrap-anywhere, which broke
    // every identifier at the edge of the line it started on (remove_handl /
    // er) even where it would have fit whole on a line of its own: break-words
    // moves it to its own line first and breaks only a name longer than that.
    contentToc: {
      slots: {
        linkText: 'whitespace-normal! break-words',
      },
    },
    // A search result is one line, cut with an ellipsis: on a 390px phone
    // "Events > Chain and nodes ...ished.node_id node_skipped" ended before
    // the word that was searched for. Two lines.
    commandPalette: {
      slots: {
        itemLabel: 'line-clamp-2 whitespace-normal! break-words',
      },
    },
    // The page body is a size container, so a table can ask how wide the text
    // column is (prose.css) instead of how wide the window is: the sidebar and
    // the contents column take 400px of a 1024px window.
    pageBody: {
      base: '@container/prose',
    },
    prose: {
      // Inline code is an inline-block, so one long path or flag is wider
      // than a phone and the whole page scrolls sideways. Let it break.
      code: {
        base: 'max-w-full wrap-break-word',
      },
      // Where a table does not fit its text column it scrolls inside its own
      // box, and scroll-hint (assets/css/prose.css) shows there is more to the
      // right. A table column is never narrower than its longest word, and
      // wrap-break-word (the code rule above) does not count a break point
      // inside a word when it works that out, so one long code span in a cell
      // kept the column wide and 126 of 252 tables scrolled sideways at 390px.
      // prose.css makes a long span wrap-anywhere, which does count it, in a
      // narrow table.
      table: {
        slots: {
          root: 'scroll-hint',
        },
      },
      // A 1440px screenshot is 343px wide on a phone, and tapping it opened
      // the same 343px image in an overlay. On a phone the overlay now shows
      // it at the screenshots' own 1440px and scrolls both ways (the width is
      // stated: @nuxt/image's 1x/2x srcset makes a browser count the file as
      // 720px, so "natural size" was half; prose.css keeps mobile.png, which
      // is phone-sized already, fitting the screen). The "safe" centering
      // keeps the left edge reachable when the image is wider than the
      // screen; an unsafe one clipped it. The overlay's dialog role, close
      // button and focus are in ProseImg.vue, a copy of Nuxt UI's component.
      img: {
        slots: {
          content: 'overflow-auto justify-center-safe! items-center-safe!',
          zoomedImage: 'max-sm:w-[1440px] max-sm:shrink-0 max-sm:max-w-none max-sm:max-h-none',
        },
      },
      // Nuxt UI soft-wraps code blocks, which breaks YAML indentation on a
      // phone. Scroll sideways instead.
      // (The copy button covering the end of a long first line is handled in
      // assets/css/prose.css, on the first line only: padding on the whole
      // block made 5 to 8 blocks that fit scroll sideways for 30px of nothing.)
      pre: {
        slots: {
          base: 'whitespace-pre wrap-normal',
        },
      },
      // Seven agent tabs don't fit a phone, and each label truncated to a
      // letter or two ("C.." for both Codex and Cursor). Keep every label
      // whole and let the strip scroll sideways instead. A scrolling list
      // clips its own border, and the selected tab's underline sat on it, so
      // the rule is an inset shadow and the underline sits just inside it.
      // scroll-hint (assets/css/prose.css) shows there is more past the edge,
      // as it does on a table.
      tabs: {
        slots: {
          list: 'scroll-hint overflow-x-auto overflow-y-hidden border-b-0 pb-[calc(var(--spacing)+1px)] shadow-[inset_0_-1px_0_var(--ui-border)]',
          indicator: 'bottom-0',
          trigger: 'shrink-0',
        },
      },
    },
  },
})
