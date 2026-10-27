# kraft-superpowers

A [Kraft](https://kraft-sdlc.dev) add-on: Kraft's shipped chains, `default` and
`quick-task`, with the methods of the
[superpowers](https://github.com/obra/superpowers) plugin on the tasks that write
the spec and the plan, implement, and repair.

```bash
kraft admin addon collection add itsOmidKarami/kraft
kraft admin addon install kraft-superpowers@kraft-sdlc
kraft item create "title" --chain kraft-superpowers:default
```

The superpowers plugin must be installed wherever the agent runs. Installing
this add-on installs `kraft-default`, which it builds on.

`library.yaml` is the whole add-on: steering that names each method, set on the
tasks it overrides. The methods are named in steering rather than `skill:`, and
the steering keeps a headless worker from asking a person anything. The
[reference](https://kraft-sdlc.dev/reference/configuration/addons#krafts-own-collection)
lists what each task is told.

A change to `library.yaml` raises `version` in `.kraft/addon.json`, or installs
never see it.
