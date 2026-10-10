import type { DraftView } from "./types";

/** `GET /drafts/chains/default` from W9's server on the shipped default chain, trimmed to six nodes. Tests only. */
export const DEFAULT_VIEW = {
 "area": "chains",
 "key": "default",
 "draft": false,
 "files": {
  "chains/default.yaml": "# The default chain (trimmed for tests)\nid: default\nnodes:\n  - id: spec\n    kind: exec\n    tasks:\n      - id: author\n        extends: spec_author\n"
 },
 "base": {
  "chains/default.yaml": "0000000000000000000000000000000000000000000000000000000000000000"
 },
 "updated_at": null,
 "plugin": null,
 "result": {
  "model": {
   "chains/default.yaml": {
    "id": "default",
    "nodes": [
     {
      "id": "spec",
      "kind": "exec",
      "steps": [
       {
        "id": "main",
        "tasks": [
         {
          "id": "author",
          "extends": "spec_author"
         }
        ]
       }
      ]
     },
     {
      "id": "spec_approval",
      "kind": "gate",
      "message": "Review and approve the specification.",
      "artifact": "spec",
      "reject_to": "spec"
     },
     {
      "id": "implementation",
      "extends": "implementation"
     },
     {
      "id": "verification",
      "extends": "verification"
     },
     {
      "id": "local_review",
      "kind": "gate",
      "message": "Approve creating a draft merge request.",
      "artifact": "work_brief",
      "reject_to": "implementation"
     },
     {
      "id": "merge_request_feedback",
      "extends": "post_draft_feedback",
      "on_base_changed": {
       "restart_from": "verification"
      }
     }
    ]
   }
  },
  "resolved": {
   "id": "default",
   "chain": {
    "id": "default",
    "nodes": [
     {
      "tasks": [
       {
        "id": "author",
        "steering": [
         "project-standards"
        ],
        "kind": "agent",
        "harness": "claude",
        "prompt": "Produce the work item's specification.",
        "skill": "kraft:spec",
        "produces": "spec"
       }
      ],
      "id": "spec",
      "kind": "exec"
     },
     {
      "id": "spec_approval",
      "kind": "gate",
      "message": "Review and approve the specification.",
      "artifact": "spec",
      "reject_to": "spec"
     },
     {
      "tasks": [
       {
        "id": "implement",
        "policy": {
         "time_cap_minutes": 120
        },
        "kind": "agent",
        "harness": "claude",
        "prompt": "Implement the approved plan.",
        "profile": "strong"
       }
      ],
      "id": "implementation",
      "kind": "exec"
     },
     {
      "steps": [
       {
        "id": "tests",
        "tasks": [
         {
          "id": "test_changed_scopes",
          "kind": "subprocess",
          "command": "true"
         }
        ]
       },
       {
        "id": "review",
        "tasks": [
         {
          "id": "code_review",
          "kind": "agent",
          "harness": "claude",
          "prompt": "Review this work item's change for defects its passing tests do not catch.",
          "skill": "kraft:code-review",
          "inputs": [
           "review_package",
           "carried_findings",
           "previous_review"
          ]
         }
        ]
       }
      ],
      "id": "verification",
      "kind": "exec",
      "fix_loop": {
       "tasks": [
        {
         "id": "repair",
         "kind": "agent",
         "harness": "claude",
         "prompt": "Fix the failing tests and the review findings this node's verification reported.",
         "profile": "strong"
        }
       ],
       "judge": {
        "id": "judge",
        "kind": "agent",
        "harness": "claude",
        "prompt": "Decide whether another repair attempt is justified.",
        "skill": "kraft:fix-loop-judge",
        "profile": "strong",
        "inputs": [
         "review_package"
        ]
       },
       "max_attempts": 2
      }
     },
     {
      "id": "local_review",
      "kind": "gate",
      "message": "Approve creating a draft merge request.",
      "artifact": "work_brief",
      "reject_to": "implementation"
     },
     {
      "steps": [
       {
        "id": "ci",
        "tasks": [
         {
          "id": "await_ci",
          "scope": "each_repository",
          "policy": {
           "total_time_cap_minutes": 90
          },
          "kind": "forge",
          "target": "mr.ci",
          "wait": {
           "polling": {
            "initial_interval": "30s",
            "max_interval": "5m"
           }
          }
         }
        ]
       },
       {
        "id": "automated_review",
        "tasks": [
         {
          "id": "await_review",
          "scope": "each_repository",
          "policy": {
           "total_time_cap_minutes": 30
          },
          "kind": "forge",
          "target": "mr.automated_review",
          "wait": {
           "polling": {
            "initial_interval": "30s",
            "max_interval": "5m"
           }
          }
         }
        ]
       }
      ],
      "id": "merge_request_feedback",
      "kind": "exec",
      "on_failure": {
       "steps": [
        {
         "id": "repair",
         "tasks": [
          {
           "id": "repair_feedback",
           "kind": "agent",
           "harness": "claude",
           "prompt": "Repair what fails the merge request's checks from outside the code, such as a missing or wrong label. A code failure is the fix loop's, which runs after you.",
           "skill": "kraft:mr-metadata-repair",
           "profile": "strong"
          }
         ]
        },
        {
         "id": "sync",
         "tasks": [
          {
           "id": "sync_mr",
           "scope": "each_repository",
           "kind": "forge",
           "target": "mr.sync"
          }
         ]
        }
       ]
      },
      "fix_loop": {
       "steps": [
        {
         "id": "repair",
         "tasks": [
          {
           "id": "repair",
           "kind": "agent",
           "harness": "claude",
           "prompt": "Resolve the current CI failures and actionable merge-request feedback.",
           "profile": "strong"
          }
         ]
        },
        {
         "id": "sync",
         "tasks": [
          {
           "id": "sync",
           "scope": "each_repository",
           "kind": "forge",
           "target": "mr.sync"
          }
         ]
        }
       ],
       "judge": {
        "id": "judge",
        "kind": "agent",
        "harness": "claude",
        "prompt": "Decide whether another repair attempt is justified.",
        "skill": "kraft:fix-loop-judge",
        "profile": "strong",
        "inputs": [
         "review_package"
        ]
       },
       "max_attempts": 3
      },
      "on_base_changed": {
       "restart_from": "verification"
      }
     }
    ]
   },
   "task_paths": [
    "spec.main.author",
    "implementation.main.implement",
    "verification.tests.test_changed_scopes",
    "verification.review.code_review",
    "verification.fix_loop.main.repair",
    "verification.fix_loop.judge",
    "merge_request_feedback.ci.await_ci",
    "merge_request_feedback.automated_review.await_review",
    "merge_request_feedback.on_failure.repair.repair_feedback",
    "merge_request_feedback.on_failure.sync.sync_mr",
    "merge_request_feedback.fix_loop.repair.repair",
    "merge_request_feedback.fix_loop.sync.sync",
    "merge_request_feedback.fix_loop.judge"
   ],
   "steering": {},
   "nodes": [
    {
     "id": "spec",
     "kind": "exec",
     "tasks": [
      "spec.main.author"
     ],
     "steps": [
      [
       "spec.main.author"
      ]
     ],
     "gate_after": null,
     "reject_to": null,
     "fix_loop": null,
     "auto_escalate": null,
     "on_failure": null,
     "covered_by": "spec",
     "icon": null
    },
    {
     "id": "spec_approval",
     "kind": "gate",
     "tasks": [],
     "steps": null,
     "gate_after": "spec_approval",
     "reject_to": "spec",
     "fix_loop": null,
     "auto_escalate": false,
     "on_failure": null,
     "covered_by": "spec",
     "icon": null
    },
    {
     "id": "implementation",
     "kind": "exec",
     "tasks": [
      "implementation.main.implement"
     ],
     "steps": [
      [
       "implementation.main.implement"
      ]
     ],
     "gate_after": null,
     "reject_to": null,
     "fix_loop": null,
     "auto_escalate": null,
     "on_failure": null,
     "covered_by": null,
     "icon": null
    },
    {
     "id": "verification",
     "kind": "exec",
     "tasks": [
      "verification.tests.test_changed_scopes",
      "verification.review.code_review"
     ],
     "steps": [
      [
       "verification.tests.test_changed_scopes"
      ],
      [
       "verification.review.code_review"
      ]
     ],
     "gate_after": null,
     "reject_to": null,
     "fix_loop": "verification.fix_loop",
     "auto_escalate": null,
     "on_failure": null,
     "covered_by": null,
     "icon": null
    },
    {
     "id": "local_review",
     "kind": "gate",
     "tasks": [],
     "steps": null,
     "gate_after": "local_review",
     "reject_to": "implementation",
     "fix_loop": null,
     "auto_escalate": false,
     "on_failure": null,
     "covered_by": "work_brief",
     "icon": null
    },
    {
     "id": "merge_request_feedback",
     "kind": "exec",
     "tasks": [
      "merge_request_feedback.ci.await_ci",
      "merge_request_feedback.automated_review.await_review"
     ],
     "steps": [
      [
       "merge_request_feedback.ci.await_ci"
      ],
      [
       "merge_request_feedback.automated_review.await_review"
      ]
     ],
     "gate_after": null,
     "reject_to": null,
     "fix_loop": "merge_request_feedback.fix_loop",
     "auto_escalate": null,
     "on_failure": [
      "merge_request_feedback.on_failure.repair.repair_feedback",
      "merge_request_feedback.on_failure.sync.sync_mr"
     ],
     "covered_by": null,
     "icon": null
    }
   ],
   "documents": {
    "spec_approval": [
     "spec"
    ],
    "local_review": [
     "spec",
     "plan",
     "chain_revision",
     "work_brief"
    ]
   }
  },
  "problems": [],
  "sources": {
   "spec": {
    "id": {
     "value": "spec",
     "source": "chain"
    },
    "kind": {
     "value": "exec",
     "source": "chain"
    },
    "on_base_changed": {
     "value": null,
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.timeout_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.max_attempts": {
     "value": null,
     "source": "default"
    },
    "policy.escalation_harness": {
     "value": "claude",
     "source": "policy"
    }
   },
   "spec.main.author": {
    "id": {
     "value": "author",
     "source": "chain"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [
      "project-standards"
     ],
     "source": "library:tasks.spec_author"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.spec_author"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.spec_author"
    },
    "prompt": {
     "value": "Produce the work item's specification.",
     "source": "library:tasks.spec_author"
    },
    "skill": {
     "value": "kraft:spec",
     "source": "library:tasks.spec_author"
    },
    "produces": {
     "value": "spec",
     "source": "library:tasks.spec_author"
    },
    "profile": {
     "value": null,
     "source": "default"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [],
     "source": "default"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "spec_approval": {
    "id": {
     "value": "spec_approval",
     "source": "chain"
    },
    "kind": {
     "value": "gate",
     "source": "chain"
    },
    "message": {
     "value": "Review and approve the specification.",
     "source": "chain"
    },
    "artifact": {
     "value": "spec",
     "source": "chain"
    },
    "artifact_required": {
     "value": false,
     "source": "default"
    },
    "reject_to": {
     "value": "spec",
     "source": "chain"
    },
    "timeout": {
     "value": null,
     "source": "default"
    },
    "chain_finalized": {
     "value": false,
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "implementation": {
    "id": {
     "value": "implementation",
     "source": "chain"
    },
    "kind": {
     "value": "exec",
     "source": "library:nodes.implementation"
    },
    "on_base_changed": {
     "value": null,
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.timeout_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.max_attempts": {
     "value": null,
     "source": "default"
    },
    "policy.escalation_harness": {
     "value": "claude",
     "source": "policy"
    }
   },
   "implementation.main.implement": {
    "id": {
     "value": "implement",
     "source": "library:nodes.implementation"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.implementer"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.implementer"
    },
    "prompt": {
     "value": "Implement the approved plan.",
     "source": "library:tasks.implementer"
    },
    "skill": {
     "value": null,
     "source": "default"
    },
    "produces": {
     "value": null,
     "source": "default"
    },
    "profile": {
     "value": "strong",
     "source": "library:tasks.implementer"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [],
     "source": "default"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": 120,
     "source": "library:tasks.implementer"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "verification": {
    "id": {
     "value": "verification",
     "source": "chain"
    },
    "kind": {
     "value": "exec",
     "source": "library:nodes.verification"
    },
    "on_base_changed": {
     "value": null,
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.timeout_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.max_attempts": {
     "value": null,
     "source": "default"
    },
    "policy.escalation_harness": {
     "value": "claude",
     "source": "policy"
    }
   },
   "verification.tests": {
    "id": {
     "value": "tests",
     "source": "library:nodes.verification"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "verification.tests.test_changed_scopes": {
    "id": {
     "value": "test_changed_scopes",
     "source": "library:nodes.verification"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "subprocess",
     "source": "library:tasks.verify_changed_scopes"
    },
    "command": {
     "value": "true",
     "source": "library:tasks.verify_changed_scopes"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "verification.review": {
    "id": {
     "value": "review",
     "source": "library:nodes.verification"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "verification.review.code_review": {
    "id": {
     "value": "code_review",
     "source": "library:nodes.verification"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.code_review"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.code_review"
    },
    "prompt": {
     "value": "Review this work item's change for defects its passing tests do not catch.",
     "source": "library:tasks.code_review"
    },
    "skill": {
     "value": "kraft:code-review",
     "source": "library:tasks.code_review"
    },
    "produces": {
     "value": null,
     "source": "default"
    },
    "profile": {
     "value": null,
     "source": "default"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [
      "review_package",
      "carried_findings",
      "previous_review"
     ],
     "source": "library:tasks.code_review"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "verification.fix_loop": {
    "max_attempts": {
     "value": 2,
     "source": "library:nodes.verification"
    }
   },
   "verification.fix_loop.main.repair": {
    "id": {
     "value": "repair",
     "source": "library:nodes.verification"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.repair_verification"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.repair_verification"
    },
    "prompt": {
     "value": "Fix the failing tests and the review findings this node's verification reported.",
     "source": "library:tasks.repair_verification"
    },
    "skill": {
     "value": null,
     "source": "default"
    },
    "produces": {
     "value": null,
     "source": "default"
    },
    "profile": {
     "value": "strong",
     "source": "library:tasks.repair_verification"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [],
     "source": "default"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "verification.fix_loop.judge": {
    "id": {
     "value": "judge",
     "source": "library:nodes.verification"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.strict_judge"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.strict_judge"
    },
    "prompt": {
     "value": "Decide whether another repair attempt is justified.",
     "source": "library:tasks.strict_judge"
    },
    "skill": {
     "value": "kraft:fix-loop-judge",
     "source": "library:tasks.strict_judge"
    },
    "produces": {
     "value": null,
     "source": "default"
    },
    "profile": {
     "value": "strong",
     "source": "library:tasks.strict_judge"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [
      "review_package"
     ],
     "source": "library:tasks.strict_judge"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "local_review": {
    "id": {
     "value": "local_review",
     "source": "chain"
    },
    "kind": {
     "value": "gate",
     "source": "chain"
    },
    "message": {
     "value": "Approve creating a draft merge request.",
     "source": "chain"
    },
    "artifact": {
     "value": "work_brief",
     "source": "chain"
    },
    "artifact_required": {
     "value": false,
     "source": "default"
    },
    "reject_to": {
     "value": "implementation",
     "source": "chain"
    },
    "timeout": {
     "value": null,
     "source": "default"
    },
    "chain_finalized": {
     "value": false,
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback": {
    "id": {
     "value": "merge_request_feedback",
     "source": "chain"
    },
    "kind": {
     "value": "exec",
     "source": "library:nodes.post_draft_feedback"
    },
    "on_base_changed": {
     "value": {
      "restart_from": "verification"
     },
     "source": "chain"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.timeout_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.max_attempts": {
     "value": null,
     "source": "default"
    },
    "policy.escalation_harness": {
     "value": "claude",
     "source": "policy"
    }
   },
   "merge_request_feedback.ci": {
    "id": {
     "value": "ci",
     "source": "library:nodes.post_draft_feedback"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.ci.await_ci": {
    "id": {
     "value": "await_ci",
     "source": "library:nodes.post_draft_feedback"
    },
    "scope": {
     "value": "each_repository",
     "source": "library:tasks.await_mr_ci"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "forge",
     "source": "library:tasks.await_mr_ci"
    },
    "target": {
     "value": "mr.ci",
     "source": "library:tasks.await_mr_ci"
    },
    "wait": {
     "value": {
      "polling": {
       "initial_interval": "30s",
       "max_interval": "5m"
      }
     },
     "source": "library:tasks.await_mr_ci"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": 90,
     "source": "library:tasks.await_mr_ci"
    }
   },
   "merge_request_feedback.automated_review": {
    "id": {
     "value": "automated_review",
     "source": "library:nodes.post_draft_feedback"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.automated_review.await_review": {
    "id": {
     "value": "await_review",
     "source": "library:nodes.post_draft_feedback"
    },
    "scope": {
     "value": "each_repository",
     "source": "library:tasks.await_automated_review"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "forge",
     "source": "library:tasks.await_automated_review"
    },
    "target": {
     "value": "mr.automated_review",
     "source": "library:tasks.await_automated_review"
    },
    "wait": {
     "value": {
      "polling": {
       "initial_interval": "30s",
       "max_interval": "5m"
      }
     },
     "source": "library:tasks.await_automated_review"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": 30,
     "source": "library:tasks.await_automated_review"
    }
   },
   "merge_request_feedback.on_failure.repair": {
    "id": {
     "value": "repair",
     "source": "library:nodes.post_draft_feedback"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.on_failure.repair.repair_feedback": {
    "id": {
     "value": "repair_feedback",
     "source": "library:nodes.post_draft_feedback"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.repair_mr_checks"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.repair_mr_checks"
    },
    "prompt": {
     "value": "Repair what fails the merge request's checks from outside the code, such as a missing or wrong label. A code failure is the fix loop's, which runs after you.",
     "source": "library:tasks.repair_mr_checks"
    },
    "skill": {
     "value": "kraft:mr-metadata-repair",
     "source": "library:tasks.repair_mr_checks"
    },
    "produces": {
     "value": null,
     "source": "default"
    },
    "profile": {
     "value": "strong",
     "source": "library:tasks.repair_mr_checks"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [],
     "source": "default"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.on_failure.sync": {
    "id": {
     "value": "sync",
     "source": "library:nodes.post_draft_feedback"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.on_failure.sync.sync_mr": {
    "id": {
     "value": "sync_mr",
     "source": "library:nodes.post_draft_feedback"
    },
    "scope": {
     "value": "each_repository",
     "source": "library:tasks.sync_draft_mr"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "forge",
     "source": "library:tasks.sync_draft_mr"
    },
    "target": {
     "value": "mr.sync",
     "source": "library:tasks.sync_draft_mr"
    },
    "wait": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.fix_loop": {
    "max_attempts": {
     "value": 3,
     "source": "library:nodes.post_draft_feedback"
    }
   },
   "merge_request_feedback.fix_loop.repair": {
    "id": {
     "value": "repair",
     "source": "library:nodes.post_draft_feedback"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.fix_loop.repair.repair": {
    "id": {
     "value": "repair",
     "source": "library:nodes.post_draft_feedback"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.repair_mr_feedback"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.repair_mr_feedback"
    },
    "prompt": {
     "value": "Resolve the current CI failures and actionable merge-request feedback.",
     "source": "library:tasks.repair_mr_feedback"
    },
    "skill": {
     "value": null,
     "source": "default"
    },
    "produces": {
     "value": null,
     "source": "default"
    },
    "profile": {
     "value": "strong",
     "source": "library:tasks.repair_mr_feedback"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [],
     "source": "default"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.fix_loop.sync": {
    "id": {
     "value": "sync",
     "source": "library:nodes.post_draft_feedback"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "read_only": {
     "value": false,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.fix_loop.sync.sync": {
    "id": {
     "value": "sync",
     "source": "library:nodes.post_draft_feedback"
    },
    "scope": {
     "value": "each_repository",
     "source": "library:tasks.sync_draft_mr"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "forge",
     "source": "library:tasks.sync_draft_mr"
    },
    "target": {
     "value": "mr.sync",
     "source": "library:tasks.sync_draft_mr"
    },
    "wait": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   },
   "merge_request_feedback.fix_loop.judge": {
    "id": {
     "value": "judge",
     "source": "library:nodes.post_draft_feedback"
    },
    "scope": {
     "value": "once",
     "source": "default"
    },
    "steering": {
     "value": [],
     "source": "default"
    },
    "skippable": {
     "value": true,
     "source": "default"
    },
    "icon": {
     "value": null,
     "source": "default"
    },
    "kind": {
     "value": "agent",
     "source": "library:tasks.strict_judge"
    },
    "harness": {
     "value": "claude",
     "source": "library:tasks.strict_judge"
    },
    "prompt": {
     "value": "Decide whether another repair attempt is justified.",
     "source": "library:tasks.strict_judge"
    },
    "skill": {
     "value": "kraft:fix-loop-judge",
     "source": "library:tasks.strict_judge"
    },
    "produces": {
     "value": null,
     "source": "default"
    },
    "profile": {
     "value": "strong",
     "source": "library:tasks.strict_judge"
    },
    "model": {
     "value": null,
     "source": "default"
    },
    "effort": {
     "value": null,
     "source": "default"
    },
    "inputs": {
     "value": [
      "review_package"
     ],
     "source": "library:tasks.strict_judge"
    },
    "fallback": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_harnesses": {
     "value": null,
     "source": "default"
    },
    "policy.token_budget": {
     "value": null,
     "source": "default"
    },
    "policy.budget_usd": {
     "value": null,
     "source": "default"
    },
    "policy.allowed_tools": {
     "value": null,
     "source": "default"
    },
    "policy.deny_tools": {
     "value": [],
     "source": "default"
    },
    "policy.grants": {
     "value": [],
     "source": "default"
    },
    "policy.sandbox": {
     "value": null,
     "source": "default"
    },
    "policy.time_cap_minutes": {
     "value": null,
     "source": "default"
    },
    "policy.total_time_cap_minutes": {
     "value": null,
     "source": "default"
    }
   }
  },
  "changes": [],
  "impact": {
   "running": 0,
   "repos": []
  },
  "policy_values": {
   "auto_escalate_delay_s": 0,
   "auto_review_attempts": 1
  },
  "choices": {
   "ref": [
    {
     "value": "kraft.verify_changed_test_scopes",
     "summary": "Run the repo's test scopes the change touches"
    },
    {
     "value": "kraft.mr_rebase",
     "summary": "Rebase the worktree onto the item's base branch"
    }
   ],
   "target": [
    {
     "value": "mr.open_draft",
     "summary": "Open the item's merge request as a draft",
     "waits": false
    },
    {
     "value": "mr.sync",
     "summary": "Push the branch and rewrite the merge request's description",
     "waits": false
    },
    {
     "value": "mr.ci",
     "summary": "Wait for the merge request's CI to pass",
     "waits": true
    },
    {
     "value": "mr.automated_review",
     "summary": "Wait for the repo's automated reviewer",
     "waits": true
    },
    {
     "value": "mr.mark_ready",
     "summary": "Mark the draft merge request ready for review",
     "waits": false
    },
    {
     "value": "mr.external_approval",
     "summary": "Wait for a person to approve the merge request",
     "waits": true
    },
    {
     "value": "mr.merge",
     "summary": "Merge, then wait until the merge lands",
     "waits": true
    },
    {
     "value": "mr.post_merge_ci",
     "summary": "Wait for CI on the base branch after the merge",
     "waits": true
    }
   ],
   "inputs": [
    {
     "value": "review_package",
     "summary": "The change under review, written to a file"
    },
    {
     "value": "carried_findings",
     "summary": "The findings the node's last measurement reported"
    },
    {
     "value": "previous_review",
     "summary": "This task's previous result and summary"
    }
   ],
   "grants": [
    {
     "value": "git-commit",
     "summary": "A plain git commit"
    },
    {
     "value": "git-rebase",
     "summary": "A plain git rebase"
    },
    {
     "value": "git-push",
     "summary": "A plain git push to the item's own branch"
    }
   ]
  },
  "warnings": []
 }
} as unknown as DraftView;
