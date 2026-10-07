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
      // 768px, and only in a table of two columns: a span of up to 14
      // characters stays whole (ProseCode adds code-long above that;
      // work_item_id was cut to work / _ite / m_id on Events, with room to
      // spare) and only a long one may break. "Whole" needs nowrap: the
      // span's max-w-full lets its column shrink below it, and a name with a
      // hyphen (--description) then broke at the hyphen. A table of three or more
      // columns, or of two with a long name in its first column, is a list of
      // blocks on a phone instead (assets/css/prose.css), each block as wide
      // as the screen, so a name is cut only if it is longer than that.
      // Where a table still does not fit it scrolls inside its own box;
      // scroll-hint (assets/css/prose.css) shows there is more to the right.
      table: {
        slots: {
          root: 'scroll-hint',
          base: 'max-md:[&:not(:has(th:nth-child(3)))_code.code-long]:wrap-anywhere max-md:[&:not(:has(th:nth-child(3)))_code:not(.code-long)]:whitespace-nowrap',
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
