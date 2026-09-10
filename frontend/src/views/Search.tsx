import { useNavigate } from "react-router-dom";
import { SearchOverlay } from "../components/SearchOverlay";

/**
 * Search as a full screen (mobile app shell design, §2). The Search bottom-nav
 * tab routes here on phone; desktop still reaches `SearchOverlay` as a modal
 * via the header button / ⌘K in `App.tsx`. Same component, same API calls —
 * only the mount point differs.
 */
export function SearchView() {
  const navigate = useNavigate();
  return <SearchOverlay embedded onClose={() => navigate("/")} />;
}
