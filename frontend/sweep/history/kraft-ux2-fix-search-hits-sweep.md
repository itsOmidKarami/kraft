# kraft/ux2-fix-search-hits-sweep

Kraft-9d8b2.22 follow-up: the sweep cells for #382 (the /ng ⌘K document and bead hits).

Rules (`waves/ux2-fix-search-hits.json`): `ng-search-doc`, `ng-item-doc`, `ng-draft-item` have no offscreen, clipped-v or contrast flag and no console error; `flow-ng-search-*` complete.

Cells added (`cases/ux2-fix-search-hits.ts`): `ng-search-doc/free` (1280 dark and light, 768, 1024; `free-long` 1280): the document dialog opened from a ⌘K hit with no work item. `ng-item-doc/open` (1280 dark and light, 768, 1024; `long` 1280): the item page at `?doc=`. `ng-draft-item/bead` (1280 dark and light, 768): the draft with "Implements <id>". Flows (`.flows.ts`): `ng-search-doc-keyboard` (⌘K, Enter on a document, Esc drops `?doc=`), `ng-search-free-doc-focus` (Esc on the dialog returns focus to where ⌘K opened).

Found by the sweep: the document viewer's dialog overflowed the viewport on the right at 768 (`.dialog` is content-box, so `width: min(760px, 100%)` plus its padding and border was 42px too wide). `.dv-dialog.dialog` is now `box-sizing: border-box`; offscreen 1 -> 0 on both cells.

No 390 cells: no /ng screen has phone cells before W17.

`node sweep/wave.mjs all` against a baseline shot on origin/main (feb6b6086): no newly flagged cells. 22 existing cells differ from the baseline with a delta that rounds to 0% (board, board-peek, composer, one `ng-chains` cell); none renders the code this PR changes, so they read as run-to-run noise. One `ng-chains/gate@1280` setup timeout on one run did not repeat.

Accepted: `nested-scroll` = 1 on the document dialogs (`.dv-body` scrolls inside the page's own scroller), as on the item page's two scrollers.
