import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IconButton } from "./IconButton";
import { iconOnlyProblem } from "./iconOnly";
import { tip } from "./Tooltip";

const button = (html: string) => {
  const host = document.createElement("div");
  host.innerHTML = html;
  return host.firstElementChild as HTMLElement;
};

describe("iconOnlyProblem: the check every rendered button passes at the end of each test (TT-1)", () => {
  it.each([
    ["an svg with no name", '<button><svg></svg></button>', "has no accessible name"],
    ["a glyph with no name", "<button>×</button>", "has no accessible name"],
    ["an icon named but without a tooltip", '<button aria-label="Close"><svg></svg></button>', "has no tooltip"],
    ["a title is not a tooltip", '<button aria-label="Close" title="Close">×</button>', "has no tooltip"],
    ["an icon with a name and a tooltip", '<button aria-label="Close" data-tip="Close"><svg></svg></button>', null],
    ["a link is held to the same rule", '<a href="/x" aria-label="New"><svg></svg></a>', "has no tooltip"],
    ["a link with a name and a tooltip", '<a href="/x" aria-label="New" data-tip="New"><svg></svg></a>', null],
    ["a button with text", "<button>Save</button>", null],
    ["text beside an icon", "<button><svg></svg> Save</button>", null],
    ["a number", "<button>3</button>", null],
    ["text only a screen reader gets", '<button><svg></svg><span class="sr-only">Close</span></button>', "has no tooltip"],
    ["a button hidden from assistive technology", '<button aria-hidden="true"></button>', null],
    ["a switch (it has its own role)", '<button role="switch" aria-checked="false"></button>', null],
  ])("%s", (_, html, problem) => expect(iconOnlyProblem(button(html))).toBe(problem));

  it("passes IconButton and tip(), which are how a button gets both", () => {
    const { container } = render(<><IconButton label="Close">×</IconButton><button type="button" {...tip("More actions")}>⋮</button></>);
    container.querySelectorAll("button").forEach((b) => expect(iconOnlyProblem(b)).toBeNull());
  });
});
