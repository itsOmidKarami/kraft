import { SearchOverlay } from "../components/SearchOverlay";

/**
 * Search as a full screen (mobile app shell design, §2). The Search bottom-nav
 * tab routes here on phone; desktop still reaches `SearchOverlay` as a modal
 * via the header button / ⌘K in `App.tsx`. Same component, same API calls —
 * only the mount point differs.
 *
 * `onClose` is a no-op here: embedded mode hides the close button and
 * backdrop, so `onClose` only fires after `SearchOverlay` has already
 * navigated to the picked result (work item, document, or gate). Navigating
 * to "/" here would race that navigation and send the user back to the
 * board instead.
 */
export function SearchView() {
  return <SearchOverlay embedded onClose={() => {}} />;
}
