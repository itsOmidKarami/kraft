# Kraft in Herdr

Shows every live [Kraft](https://kraft-sdlc.dev/) work item in [Herdr](https://herdr.dev/) as its own tab, with its log and a sidebar state: working, blocked when a person is needed, or idle.

_Added in next_

## Install

It needs Herdr 0.9.2 or newer (checked against 0.9.5), Python 3.9 or newer, and `kraft` on the `PATH`, recent enough to have `kraft view events --all`.

```bash
herdr plugin install itsOmidKarami/kraft/plugins/herdr
```

## What it does

- A `Kraft` workspace holds one tab per live work item. Each tab follows the item's newest worker session's log.
- Herdr's sidebar shows each item as an agent named `kraft`. The state follows the item's status: `running` is working, `needs_you` and `failed` are blocked, and everything else is idle.
- The **Take over this Kraft item** action opens `kraft item takeover` for the focused item tab.
- It talks to Kraft only through the `kraft` command and to Herdr only through the `herdr` command.

The [guide](https://kraft-sdlc.dev/guides/day-to-day/herdr) covers the states and labels, the `config.json` options and taking an item over.
