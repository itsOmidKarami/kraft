/** The shipped UI's page for an `/ng` location (spec §2.6): where a phone
 *  width is sent, and where "Current UI ↗" points. Keeps the query string. */
export function legacyPath({ pathname, search }: { pathname: string; search: string }): string {
  const [a, b, c, d] = pathname.replace(/^\/ng(?=\/|$)/, "").split("/").filter(Boolean);
  let path = "/";
  let hash = "";
  if (!a || ["archived", "analytics", "search"].includes(a)) path = a ? `/${a}` : "/";
  else if (a === "work-items" && b && b !== "new") {
    path = `/work-items/${b}`;
    if (c === "nodes" && d) hash = `#node=${d}`;
    else if (c === "review") hash = "#tab=changes";
  } else if (a === "templates" && b) path = `/settings/${b}`;
  else if (a === "settings" && b) path = b === "about" ? "/settings" : `/settings/${{ notifications: "notify", "auto-intake": "intake" }[b] ?? b}`;
  return path + search + hash;
}
