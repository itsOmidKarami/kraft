import { describe, expect, it } from "vitest";
import { linkTokens } from "../../src/core/links";
import { item } from "./fixtures/item";

describe("linkTokens", () => {
  it.each([
    ["nothing to link to", item(), []],
    ["a merge request on the web", item({ mr_ref: { number: 7, url: "https://git.example/r/-/merge_requests/7" } }), ["mr"]],
    ["an http merge request, any case", item({ mr_ref: { number: 7, url: "HTTP://git.example/7" } }), ["mr"]],
    ["a merge request with no url", item({ mr_ref: { number: 7, url: "" } }), []],
    ["a merge request url that is not the web", item({ mr_ref: { number: 7, url: "vscode://x" } }), []],
    ["spec before plan, whatever the filing order", item({ attachments: [{ kind: "plan", path: "p.md" }, { kind: "spec", path: "s.md" }] }), ["attSpec", "attPlan"]],
    ["a merge request and a plan", item({ mr_ref: { number: 1, url: "https://g/1" }, attachments: [{ kind: "plan", path: "p.md" }] }), ["mr", "attPlan"]],
  ])("%s", (_, it_, want) => {
    expect(linkTokens(it_)).toEqual(want);
  });
});
