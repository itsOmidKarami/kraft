import type { Page } from "@playwright/test";

/**
 * Machine checks that need no eyes. Each is a count plus up to 8 examples,
 * so the manifest can be sorted by "how bad" and a reviewer can see what.
 */
export interface Checks {
  pageOverflowX: boolean;
  offscreenRight: { count: number; examples: string[] };
  clippedEllipsis: { count: number; examples: string[] };
  clippedVertical: { count: number; examples: string[] };
  smallTargets: { count: number; examples: string[] };   // phone only
  smallInputs: { count: number; examples: string[] };    // phone only
  nestedScrollers: { count: number; examples: string[] };
  negativeDurations: { count: number; examples: string[] };
  lowContrast: { count: number; examples: string[] };
  consoleErrors: string[];
  setupError?: string;
}

export async function runChecks(page: Page, phone: boolean, consoleErrors: string[]): Promise<Checks> {
  const inPage = await page.evaluate((phone) => {
    const vw = window.innerWidth;
    const ex = (el: Element) => {
      const e = el as HTMLElement;
      const label = (e.getAttribute("aria-label") || e.getAttribute("data-testid") || e.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60);
      const cls = e.className && typeof e.className === "string" ? "." + e.className.split(" ").filter(Boolean).slice(0, 2).join(".") : "";
      return `${e.tagName.toLowerCase()}${cls}${label ? ` "${label}"` : ""}`;
    };
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return false;
      const cs = getComputedStyle(el);
      return cs.visibility !== "hidden" && cs.display !== "none" && cs.opacity !== "0";
    };
    const all = [...document.querySelectorAll("body *")].filter(visible);
    const bucket = (arr: Element[]) => ({ count: arr.length, examples: arr.slice(0, 8).map(ex) });

    // Past the right edge and not reachable. The nearest ancestor that clips
    // horizontally decides: a scroll container (overflow-x auto/scroll) that
    // is itself inside the viewport and whose row the element sits in makes
    // it reachable by scrolling, so it is not offscreen. `hidden`/`clip`, or
    // no clipping ancestor at all (the viewport cuts it), still counts.
    // Overlap is vertical only: a scroller that stops short of the edge (a
    // chip row with buttons beside it) holds chips scrolled wholly past its
    // own right edge that still cross the viewport's.
    const scrollable = (el: Element, r: DOMRect) => {
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        const ox = getComputedStyle(a).overflowX;
        if (ox === "visible") continue;
        if (ox !== "auto" && ox !== "scroll") return false;
        const ar = a.getBoundingClientRect();
        // The exemption is for a strip that scrolls sideways, not for a panel
        // whose x-scroll is a side effect of overflow-y (W10.D): the peek, a
        // sheet or dialog itself, a fixed-positioned panel, or a scroller that
        // fills the page (>80% of the viewport wide and over half its height).
        // Content past such a panel's edge is cut off for the reader.
        const panel = getComputedStyle(a).position === "fixed" || a.matches('[role="dialog"], [aria-modal="true"], [aria-label="peek"]')
          || (ar.width > vw * 0.8 && ar.height > window.innerHeight * 0.5);
        if (panel) return false;
        const inView = ar.left >= -1 && ar.right <= vw + 1;
        const inRow = r.top < ar.bottom && r.bottom > ar.top;
        return inView && inRow;
      }
      return false;
    };
    const offscreen = all.filter((el) => {
      const r = el.getBoundingClientRect();
      return r.right > vw + 1 && r.left < vw && getComputedStyle(el).position !== "fixed" && !scrollable(el, r);
    });
    const clipped = all.filter((el) => {
      const cs = getComputedStyle(el);
      // data-allow-ellipsis: a deliberate one-line cut with the whole text in its
      // title (the Documents list path, W10.D). Only the element carrying it.
      return cs.textOverflow === "ellipsis" && cs.overflow !== "visible" && el.scrollWidth > el.clientWidth + 1 && !el.hasAttribute("data-allow-ellipsis");
    });
    const clippedV = all.filter((el) => {
      const cs = getComputedStyle(el);
      if (!(cs.overflowY === "hidden" || cs.overflow === "hidden")) return false;
      if (cs.textOverflow === "ellipsis" || (cs as any).webkitLineClamp !== "none") return false;
      return el.scrollHeight > el.clientHeight + 2 && el.children.length < 4 && (el.textContent || "").trim().length > 0;
    });
    const targets = phone
      ? [...document.querySelectorAll('button, a[href], [role="button"], input[type="checkbox"], input[type="radio"], select')]
          .filter(visible)
          .filter((el) => { const r = el.getBoundingClientRect(); return r.height < 44 || r.width < 44; })
      : [];
    const inputs = phone
      // iOS Safari zooms on focus under 16px after rounding, so 15.5px and up passes (W14 · C.3).
      ? [...document.querySelectorAll("input, textarea, select")].filter(visible).filter((el) => parseFloat(getComputedStyle(el).fontSize) < 15.5)
      : [];
    const scrollers = all.filter((el) => {
      const cs = getComputedStyle(el);
      return /(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 2;
    });
    const nested = scrollers.filter((el) => scrollers.some((o) => o !== el && o.contains(el)));

    // Text nodes, once: neg-duration and contrast both read them.
    const texts: Text[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if ((n.textContent || "").trim() && n.parentElement && visible(n.parentElement)) texts.push(n as Text);
    }
    const negDur = texts.filter((n) => /-\d+s\b/.test(n.textContent || "")).map((n) => n.parentElement!);

    // WCAG contrast of the text colour against the composited background:
    // walk up until an opaque background, alpha-blending translucent layers on
    // the way (and ancestor opacity into the text). Canvas normalises any
    // computed colour syntax (rgb, color(srgb …), oklab) to RGBA bytes.
    const cv = document.createElement("canvas"); cv.width = cv.height = 1;
    const cx = cv.getContext("2d", { willReadFrequently: true })!;
    const cache = new Map<string, number[]>();
    const rgba = (s: string) => {
      let v = cache.get(s);
      if (!v) { cx.clearRect(0, 0, 1, 1); cx.fillStyle = "#000"; cx.fillStyle = s; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data; v = [d[0], d[1], d[2], d[3] / 255]; cache.set(s, v); }
      return v;
    };
    // "a(b, c), d" → ["a(b, c)", "d"]: computed multi-layer values split on top-level commas only.
    const topLevel = (s: string) => { const out: string[] = []; let d = 0, cur = ""; for (const ch of s) { if (ch === "(") d++; else if (ch === ")") d--; if (ch === "," && d === 0) { out.push(cur.trim()); cur = ""; } else cur += ch; } out.push(cur.trim()); return out; };
    const over = (top: number[], under: number[]) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat(1);
    const lum = (c: number[]) => { const f = (x: number) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
    const low: string[] = [];
    let lowCount = 0;
    const seenEl = new Set<Element>();
    for (const n of texts) {
      const el = n.parentElement!;
      if (seenEl.has(el)) continue;
      seenEl.add(el);
      const r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > vw || r.width <= 1 || r.height <= 1) continue;
      // Not text a reader has to read: disabled controls, placeholders, and
      // anything hidden from assistive tech. Muted/secondary text is NOT
      // exempt — that is exactly what the check is for.
      if (el.closest("[disabled], [aria-disabled='true'], option, [aria-hidden='true'], .placeholder, [data-placeholder]")) continue;
      const cs = getComputedStyle(el);
      // Each layer is its alternatives: one colour for a background-color, every
      // colour stop for a gradient (Kraft-aqrs9). The text has to clear its bar
      // against the worst stop, since some part of the gradient sits under it.
      const layers: number[][][] = [];
      let alpha = 1;
      let bg: number[] | null = null;
      let unknown = false;
      for (let a: Element | null = el; a; a = a.parentElement) {
        const acs = getComputedStyle(a);
        alpha *= parseFloat(acs.opacity);
        // Image layers top first, each paired with its own background-size.
        const sizes = topLevel(acs.backgroundSize);
        topLevel(acs.backgroundImage).forEach((img, i) => {
          if (img === "none") return;
          // A hairline (a 1px divider drawn as a gradient) is not a ground under text.
          // ponytail: judged by background-size only, a positioned small tile would also be skipped.
          if ((sizes[i % sizes.length] ?? "auto").split(/\s+/).some((s) => /px$/.test(s) && parseFloat(s) < 4)) return;
          const stops = img.includes("url(") ? [] : (img.match(/(rgba?|color|oklab|oklch|lab|lch|hsla?)\([^)]*\)|#[0-9a-f]{3,8}\b|transparent/gi) ?? []);
          if (stops.length) layers.push(stops.map(rgba));
          else unknown = true;
        });
        const b = rgba(acs.backgroundColor);
        if (b[3] >= 0.999) { bg = b; break; }
        if (b[3] > 0) layers.push([b]);
      }
      // Faded below half opacity with no role and outside any control or
      // link: decoration (a ghost watermark, a dimmed separator), not copy.
      // Opacity on the path only up to the opaque ground is what shows.
      if (alpha < 0.5 && !el.closest("[role], button, a")) continue;
      // An image ground the check cannot sample fails as unknown, never passes blind.
      if (unknown) { lowCount++; if (low.length < 8) low.push(`${ex(el)} gradient`); continue; }
      let grounds = [bg ?? [255, 255, 255, 1]];
      for (const alts of layers.reverse()) grounds = grounds.flatMap((g) => alts.map((c) => over(c, g))).slice(0, 64);
      const fg0 = rgba(cs.color);
      let ratio = Infinity;
      for (const g of grounds) {
        const fg = over([fg0[0], fg0[1], fg0[2], fg0[3] * alpha], g);
        const [L1, L2] = [lum(fg), lum(g)].sort((x, y) => y - x);
        ratio = Math.min(ratio, (L1 + 0.05) / (L2 + 0.05));
      }
      const min = parseFloat(cs.fontSize) >= 24 ? 3 : 4.5;
      if (ratio < min) { lowCount++; if (low.length < 8) low.push(`${ex(el)} ${ratio.toFixed(2)}:1`); }
    }

    return {
      pageOverflowX: document.documentElement.scrollWidth > vw + 1,
      offscreenRight: bucket(offscreen),
      clippedEllipsis: bucket(clipped),
      clippedVertical: bucket(clippedV),
      smallTargets: bucket(targets),
      smallInputs: bucket(inputs),
      nestedScrollers: bucket(nested),
      negativeDurations: bucket(negDur),
      lowContrast: { count: lowCount, examples: low },
    };
  }, phone);
  return { ...inPage, consoleErrors: [...consoleErrors] };
}

/**
 * The focused element shows no ring: its outline/box-shadow/border/background
 * equal those of an unfocused clone of itself, and its parent draws no
 * :focus-within ring. A clone, not blur(): blur fires onBlur handlers.
 *
 * `anyFocus` judges document.activeElement however it got focus — on the
 * flow steps driven from the keyboard a ring that only a mouse user would
 * miss is still missing. Otherwise only a :focus-visible element is judged
 * (Kraft-s400i: mouse-focused elements never reached the check).
 */
export async function focusRingMissing(page: Page, anyFocus: boolean): Promise<boolean> {
  return page.evaluate((anyFocus) => {
    const a = document.activeElement as HTMLElement | null;
    if (!a || a === document.body || !a.parentElement) return false;
    if (!anyFocus && !a.matches(":focus-visible")) return false;
    const keys = ["outlineStyle", "outlineWidth", "outlineColor", "boxShadow", "borderColor", "backgroundColor"] as const;
    const cs = getComputedStyle(a);
    const now = keys.map((k) => cs[k]);
    const c = a.cloneNode(false) as HTMLElement;
    c.removeAttribute("id"); c.removeAttribute("autofocus"); c.tabIndex = -1;
    c.style.position = "absolute"; c.style.visibility = "hidden"; c.style.pointerEvents = "none";
    a.parentElement.insertBefore(c, a.nextSibling);
    const cc = getComputedStyle(c);
    const differs = keys.some((k, i) => cc[k] !== now[i]);
    c.remove();
    if (differs) return false;
    const p = getComputedStyle(a.parentElement);
    if (a.parentElement.matches(":focus-within") && ((p.outlineStyle !== "none" && parseFloat(p.outlineWidth) > 0) || p.boxShadow !== "none")) return false;
    return true;
  }, anyFocus);
}

/** Scroll every scroll container (and the window) to its bottom. */
export async function scrollAllToBottom(page: Page) {
  await page.evaluate(() => {
    window.scrollTo(0, document.documentElement.scrollHeight);
    for (const el of document.querySelectorAll<HTMLElement>("body *")) {
      const cs = getComputedStyle(el);
      if (/(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 2) el.scrollTop = el.scrollHeight;
    }
  });
  await page.waitForTimeout(150);
}

/** Positions of the chrome that must not move when content scrolls. */
export async function chromeRects(page: Page) {
  return page.evaluate(() => {
    const pick = (sel: string) => { const el = document.querySelector(sel); return el ? Math.round(el.getBoundingClientRect().top) : null; };
    return { header: pick("header, .app-header, .detail-head"), actionBar: pick(".item-card-actions, .item-actions, .control-row"), tabs: pick('[role="tablist"]'), stageGraph: pick(".stage-graph") };
  });
}
