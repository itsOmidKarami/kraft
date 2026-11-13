<!-- Why Kraft: the docs page that was /get-started/why-kraft, moved here as
     written, one section per block. Its old address and section anchors
     forward here (content/redirects.yml). Its links go to the docs. -->
<template>
  <div>
    <SitePageHead
      label="WHY KRAFT"
      title="Not another coding agent."
    >
      <p>Kraft isn't a coding agent, and it doesn't replace yours. It runs the agent you already use, and adds what a single session can't give you.</p>
      <p>Use Kraft when you want agent work to run while you aren't watching, and to stop only where a person should decide. Use an interactive session for everything else.</p>
      <p>In a session you are the scheduler, the reviewer, the test runner and the memory. Close the terminal and the work stops. Kraft takes over those jobs: items run unattended, several at once, and the board tells you which ones need you.</p>
    </SitePageHead>

    <SiteBlock
      id="what-kraft-adds"
      title="What Kraft adds."
    >
      <div class="s-prose">
        <p>Three claims, then the two limits that keep them safe to leave running:</p>
        <ul>
          <li><strong>A process the agent can't skip.</strong> A <NuxtLink to="/concepts/vocabulary#chain">chain</NuxtLink> is a file you can read and diff, from spec to merge, so the agent doesn't decide the workflow fresh each time.</li>
          <li><strong>Checks it doesn't grade itself on.</strong> Kraft runs your tests and waits for your CI in nodes of their own, and a separate reviewer or judge decides whether a fix is done.</li>
          <li><strong>A person at the decisions.</strong> At a <NuxtLink to="/concepts/vocabulary#gate">gate</NuxtLink> you approve a spec, a plan or a merge request. A gate stops the chain until a decision is recorded, and an agent's own calls to decide its gate are refused. Only a sandbox with a <code>network:</code> policy keeps an agent from deciding its own gates; no sandbox is set by default, and an unsandboxed agent runs as you. <NuxtLink to="/project/security-in-depth#agents-and-gates">Security</NuxtLink> has the scope.</li>
          <li><strong>Bounded retries and spend.</strong> Time, token and dollar caps apply per work item, node, step and task, and a defect the agent can't fix stops the item with the full trace. See <NuxtLink to="/concepts/caps-and-budgets">Caps and budgets</NuxtLink>.</li>
          <li><strong>Isolation.</strong> Every <NuxtLink to="/concepts/vocabulary#work-item">work item</NuxtLink> edits its own git <NuxtLink to="/concepts/vocabulary#worktree">worktree</NuxtLink> on its own branch by default, so your checkout stays as you left it and items run in parallel. A repo can <NuxtLink to="/guides/run/run-items-in-place">run its items in its own checkout</NuxtLink> instead, one at a time.</li>
        </ul>
      </div>
    </SiteBlock>

    <SiteBlock
      id="what-else-comes-with-it"
      band
      title="What else comes with it."
    >
      <div class="s-prose">
        <ul>
          <li><strong>One board.</strong> All your repos in one view, grouped by what needs you, what is running and what is done, with search and <NuxtLink to="/reference/web-ui/analytics">analytics</NuxtLink> (lead time, cost by node) from the same events. A tunnel reaches it from a phone: see <NuxtLink to="/guides/run/remote-access">Remote access</NuxtLink>.</li>
          <li><strong>Permission prompts answered.</strong> A worker has nobody to press "allow", so Kraft answers from your policy and records each allow and deny on the item's timeline. See <NuxtLink to="/concepts/permission-gate">Why a permission gate</NuxtLink>.</li>
          <li><strong>Several agents and models.</strong> Seven agents run as <NuxtLink to="/reference/harnesses">harnesses</NuxtLink>, and each agent task picks its own harness and model, so one agent can write the code and another review it. The shipped chains run every task on Claude Code; to mix them, see <NuxtLink to="/guides/harnesses/switch-harness">Switch a task to another harness</NuxtLink>.</li>
          <li><strong>Several ways in.</strong> File work from the UI, the CLI, an agent over MCP or a <NuxtLink to="/concepts/vocabulary#trigger">trigger</NuxtLink> on a schedule or a webhook, and it lands paused and spends nothing until you start it. Only <NuxtLink to="/concepts/vocabulary#auto-intake">auto-intake</NuxtLink>, off by default, starts work on its own: see <NuxtLink to="/guides/day-to-day/file-a-work-item">File a work item</NuxtLink>.</li>
        </ul>
      </div>
    </SiteBlock>

    <SiteBlock
      id="what-a-run-looks-like"
      title="What a run looks like."
    >
      <div class="s-prose">
        <p>Here is one work item on the shipped <code>default</code> chain, from filing to merge. <NuxtLink to="/concepts/how-a-work-item-runs">How a work item runs</NuxtLink> has the whole run, node by node.</p>
        <ol>
          <li>You file it with a title and, if you have them, a spec and a plan. It lands paused.</li>
          <li>You start it. An agent drafts a spec and a plan, and the item stops at a gate after each for you to approve or reject with a note. A third gate, for a plan that changes the rest of the chain, asks only when it does.</li>
          <li>An agent implements the plan, and Kraft runs the repo's tests itself.</li>
          <li>The item stops at <code>local_review</code> with a diff to read. When you approve, Kraft opens a draft merge request and runs CI and automated review on it.</li>
          <li>You approve <code>final_review</code>. Kraft marks the request ready, waits for its approval on your <NuxtLink to="/concepts/vocabulary#forge">forge</NuxtLink>, merges and waits for CI on the merged commit.</li>
        </ol>
        <p>Four stops are always yours: the spec, the plan, <code>local_review</code> and <code>final_review</code>. <NuxtLink to="/get-started/first-work-item">Your first work item</NuxtLink> and <NuxtLink to="/get-started/the-real-chain">The real chain</NuxtLink> do this on your own repo. Setup is <NuxtLink to="/get-started/install">Install</NuxtLink>, then connecting the repo.</p>
      </div>
    </SiteBlock>

    <SiteBlock
      id="how-it-compares"
      band
      title="How it compares."
    >
      <div class="s-prose">
        <h3 id="a-loop">
          A loop
        </h3>
        <p>A loop runs an agent again and again until the agent decides it is done: a <code>while</code> loop around an agent, a Ralph-style run that feeds it the same prompt each time, or <code>/loop</code>. Nothing bounds that decision. A loop spends tokens on a defect it can't fix and has no way to ask a person. Kraft's retry loops stop at an attempt limit, a wall-clock limit or a token or dollar budget, and then escalate to you.</p>
        <h3 id="skills-and-plugins-alone">
          Skills and plugins alone
        </h3>
        <p>A skill tells an agent how to do one step well, and the agent still decides whether to run it, in what order, and when to stop. That has two costs:</p>
        <ul>
          <li><strong>It is inconsistent.</strong> The process lives in the agent's judgement, so the same request can skip a review one day and run it the next. Nothing stops at your approval unless the agent chooses to, and nothing resumes after a crashed session. In a chain the order, the gates and the retries are fixed by the file, not decided at run time.</li>
          <li><strong>The context keeps growing.</strong> Every step, tool call and result stays in one conversation, and each turn sends all of it again. In Kraft each agent task is its own headless session. The chain does the sequencing and bookkeeping, and Kraft runs the repo's tests, the rebase and the merge-request steps itself (<code>builtin</code> and <code>forge</code> tasks, see <NuxtLink to="/concepts/vocabulary#task">Task</NuxtLink>), with no model call.</li>
        </ul>
        <p>Kraft runs your skills as tasks inside a chain.</p>
        <h3 id="kraft-lite">
          Kraft Lite
        </h3>
        <p><NuxtLink to="/guides/kraft-lite">Kraft Lite</NuxtLink> runs the same chain inside one agent session when you don't want a service. It keeps the fixed order and gates, but not the fresh-session saving, because it runs in the one session.</p>
        <h3 id="ci">
          CI
        </h3>
        <p>CI runs a fixed script on a fresh runner. Kraft runs an agent on your machine that you can pause, steer or reject with a note. Kraft doesn't replace CI. It watches your merge request's pipeline and fixes what fails.</p>
      </div>
    </SiteBlock>

    <SiteBlock
      id="when-not-to-use-kraft"
      title="When not to use Kraft."
    >
      <div class="s-prose">
        <ul>
          <li><strong>A quick fix.</strong> Open a session. A chain is overhead you won't earn back.</li>
          <li><strong>Exploration.</strong> If you don't yet know what you want, you need a conversation, not a pipeline. Explore first, then hand off a spec.</li>
          <li><strong>Work you want to steer every few minutes.</strong> That is what a session is for.</li>
          <li><strong>A shared team service.</strong> Kraft runs on one machine and binds loopback by default. It isn't a hosted service.</li>
          <li><strong>A repo that isn't set up for it.</strong> Kraft stops a repo's first item rather than guess its <code>test_command</code> or <code>setup_command</code>. See <NuxtLink to="/troubleshooting/launch-and-setup-stops">Launch and setup stops</NuxtLink> for the first-run stops.</li>
        </ul>
        <p>The usual pattern uses both: think in a session, settle a spec and a plan, hand them to Kraft, and take the next thing.</p>
      </div>
    </SiteBlock>

    <SiteBlock
      id="the-name"
      band
      title="The name."
    >
      <div class="s-prose">
        <p>In German, <em>Kraft</em> means force; in English, its cousin <em>craft</em> means skill. A coding agent has the skill. Kraft adds the force: a process that moves the work forward, step by step, and stops only when a decision is yours.</p>
      </div>
    </SiteBlock>

    <SiteInstall>
      <template #links>
        <SiteArrowLink :to="INSTALL">
          Install guide
        </SiteArrowLink>
        <SiteArrowLink to="/get-started/first-work-item">
          Your first work item
        </SiteArrowLink>
        <SiteArrowLink to="/get-started/faq">
          FAQ
        </SiteArrowLink>
        <SiteArrowLink to="/concepts/vocabulary">
          Vocabulary
        </SiteArrowLink>
        <SiteArrowLink to="/project/security">
          Security
        </SiteArrowLink>
        <SiteArrowLink to="/project/data-and-privacy">
          Data and privacy
        </SiteArrowLink>
      </template>
    </SiteInstall>
  </div>
</template>
