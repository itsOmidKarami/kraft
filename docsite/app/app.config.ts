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
    // wrap-anywhere lets it break there too.
    contentToc: {
      slots: {
        linkText: 'whitespace-normal! wrap-anywhere',
      },
    },
    prose: {
      // Inline code is an inline-block, so one long path or flag is wider
      // than a phone and the whole page scrolls sideways. Let it break.
      code: {
        base: 'max-w-full wrap-break-word',
      },
      // A table column is never narrower than its longest word, and
      // wrap-break-word (above) does not count a break point inside a word
      // when it works that out, so one long code span in a cell kept the
      // column wide and 126 of 252 tables scrolled sideways at 390px.
      // wrap-anywhere does count it, so the column can shrink to fit. Under
      // 640px only: wider, it let a column shrink below its own identifier
      // (work_item_ / id) to make room for its neighbour's prose.
      // Where a table still does not fit it scrolls inside its own box;
      // scroll-hint (assets/css/prose.css) shows there is more to the right.
      table: {
        slots: {
          root: 'scroll-hint',
          base: 'max-sm:[&_code]:wrap-anywhere',
        },
      },
      // A 1440px screenshot is 343px wide on a phone, and tapping it opened
      // the same 343px image in an overlay. On a phone the overlay now shows
      // it at the screenshots' own 1440px and scrolls both ways (the width is
      // stated: @nuxt/image's 1x/2x srcset makes a browser count the file as
      // 720px, so "natural size" was half; prose.css keeps mobile.png, which
      // is phone-sized already, fitting the screen). The "safe" centering keeps the left edge
      // reachable when the image is wider than the screen; an unsafe one
      // clipped it.
      img: {
        slots: {
          content: 'overflow-auto justify-center-safe! items-center-safe!',
          zoomedImage: 'max-sm:w-[1440px] max-sm:shrink-0 max-sm:max-w-none max-sm:max-h-none',
        },
      },
      // Nuxt UI pads every cell 16px a side, which is 128px of a 343px phone
      // table spent on four columns of padding. 8px on a phone, 16px from
      // 640px up.
      th: { base: 'px-2 sm:px-4' },
      td: { base: 'px-2 sm:px-4' },
      // Nuxt UI soft-wraps code blocks, which breaks YAML indentation on a
      // phone. Scroll sideways instead.
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
      tabs: {
        slots: {
          list: 'overflow-x-auto overflow-y-hidden border-b-0 pb-[calc(var(--spacing)+1px)] shadow-[inset_0_-1px_0_var(--ui-border)]',
          indicator: 'bottom-0',
          trigger: 'shrink-0',
        },
      },
    },
  },
})
