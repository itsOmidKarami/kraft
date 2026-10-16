/** How a search matches, in the words both trees show: Hybrid blends keywords and meaning. */
export const SEARCH_MODES = [{ value: "hybrid", label: "Hybrid" }, { value: "fts", label: "Keywords" }, { value: "vector", label: "Meaning" }] as const;
export type SearchMode = (typeof SEARCH_MODES)[number]["value"];
