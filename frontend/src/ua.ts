/** Device·browser, the way the Sessions table reads it (design 33). Best-effort
 *  from the User-Agent string the server already stores as a session's `label`
 *  -- never a hardware claim ("MacBook Pro" would be a guess a plain "Mac" is
 *  not), just enough to tell two of your own sessions apart at a glance. */
export function parseUserAgent(ua: string | null | undefined): string {
  if (!ua) return "unknown device";
  const device = /ipad/i.test(ua)
    ? "iPad"
    : /iphone/i.test(ua)
      ? "iPhone"
      : /android/i.test(ua)
        ? "Android"
        : /macintosh|mac os x/i.test(ua)
          ? "Mac"
          : /windows/i.test(ua)
            ? "Windows PC"
            : /linux/i.test(ua)
              ? "Linux"
              : "unknown device";
  const browser = /edg\//i.test(ua)
    ? "Edge"
    : /firefox\//i.test(ua)
      ? "Firefox"
      : /chrome\//i.test(ua) && !/chromium/i.test(ua)
        ? "Chrome"
        : /safari\//i.test(ua) && /version\//i.test(ua)
          ? "Safari"
          : "browser";
  return `${device} · ${browser}`;
}
