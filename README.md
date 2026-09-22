# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                     |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py       |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py              |      153 |        5 |     97% |335, 389-390, 392, 718 |
| src/kraft/adapters/artifact\_notes.py    |        1 |        0 |    100% |           |
| src/kraft/adapters/beads.py              |       62 |       28 |     55% |40-45, 49, 82-88, 122, 125-126, 178-190 |
| src/kraft/adapters/forge/\_\_init\_\_.py |        8 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py           |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/gh.py           |      152 |       14 |     91% |172, 252, 276, 282-292, 314-315, 370, 421 |
| src/kraft/adapters/forge/git.py          |      104 |        4 |     96% |225-232, 369 |
| src/kraft/adapters/forge/glab.py         |      176 |        9 |     95% |239-240, 327, 359-360, 426, 429, 442, 464 |
| src/kraft/adapters/forge/models.py       |      156 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py           |      124 |        5 |     96% |74, 197, 249, 315-316 |
| src/kraft/adapters/forge/run.py          |      436 |       11 |     97% |401, 585-587, 828-829, 859-870, 1362, 1455 |
| src/kraft/adapters/profiles.py           |       33 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py         |      271 |       10 |     96% |235-236, 250-251, 278, 423-424, 597-598, 743 |
| src/kraft/analytics.py                   |      252 |        4 |     98% |52-53, 181, 187 |
| src/kraft/api/\_\_init\_\_.py            |       30 |        0 |    100% |           |
| src/kraft/api/deps.py                    |      284 |       21 |     93% |82-83, 210-211, 327, 330-332, 385-386, 422-423, 432-433, 566-567, 602, 605, 608, 631-632 |
| src/kraft/api/perimeter.py               |       70 |        0 |    100% |           |
| src/kraft/api/routes/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py        |       95 |        1 |     99% |       234 |
| src/kraft/api/routes/auth.py             |       45 |        6 |     87% |     54-61 |
| src/kraft/api/routes/board.py            |      120 |        1 |     99% |       160 |
| src/kraft/api/routes/gates.py            |      128 |        9 |     93% |109, 112, 120, 288, 310, 343-344, 369-370 |
| src/kraft/api/routes/harnesses.py        |      142 |        8 |     94% |37-38, 55-56, 73, 271-272, 274 |
| src/kraft/api/routes/lifecycle.py        |      559 |       30 |     95% |142, 145-146, 151-152, 231, 260, 275, 461, 585-586, 638, 672, 677, 708, 717, 776, 868-869, 973, 1058, 1088-1089, 1152, 1172-1173, 1293-1294, 1333, 1343 |
| src/kraft/api/routes/repos.py            |      168 |        6 |     96% |64-68, 97, 116, 254-255 |
| src/kraft/api/routes/search.py           |      117 |       14 |     88% |47-48, 74-77, 80, 107-111, 134, 140-141, 171 |
| src/kraft/api/routes/sessions.py         |      133 |        9 |     93% |90, 124, 128, 267-269, 273, 275, 278 |
| src/kraft/api/routes/settings.py         |      380 |       28 |     93% |78-79, 117-118, 129-130, 193-194, 248, 291-292, 320-321, 411-414, 417, 483-486, 489-490, 494-499, 556, 558 |
| src/kraft/api/routes/work\_items.py      |      290 |       14 |     95% |131, 306-309, 419-420, 583, 600, 617-618, 703-704, 756-757, 762 |
| src/kraft/api/startup.py                 |      130 |        4 |     97% |79-81, 138-139 |
| src/kraft/archive.py                     |       29 |        6 |     79% |     52-57 |
| src/kraft/auth.py                        |       74 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py       |       48 |        8 |     83% |140-148, 181-186 |
| src/kraft/automated\_review.py           |       12 |        0 |    100% |           |
| src/kraft/builtins.py                    |      312 |       18 |     94% |79, 90-91, 99, 183, 249, 340-342, 383-384, 398-399, 657, 742-743, 1096-1099 |
| src/kraft/cap\_levels.py                 |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                |       22 |        0 |    100% |           |
| src/kraft/caps.py                        |      269 |       17 |     94% |291, 317, 328, 340-341, 354, 451, 523, 568-570, 608-613 |
| src/kraft/cli/\_\_init\_\_.py            |       39 |        1 |     97% |       118 |
| src/kraft/cli/admin.py                   |      452 |       38 |     92% |72, 155-157, 175, 181-185, 194, 279, 306, 347, 394-395, 433-434, 464, 555, 644, 682-684, 714-715, 719-723, 732-738, 911 |
| src/kraft/cli/common.py                  |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                    |      186 |        6 |     97% |30, 116, 124, 172, 176, 192 |
| src/kraft/cli/repo.py                    |       67 |        4 |     94% |69-70, 82-83 |
| src/kraft/cli/templates.py               |       67 |        2 |     97% |    72, 83 |
| src/kraft/cli/view.py                    |      194 |        8 |     96% |90-94, 176-177, 201, 211, 261-262 |
| src/kraft/client/\_\_init\_\_.py         |        4 |        0 |    100% |           |
| src/kraft/client/actions.py              |      148 |       29 |     80% |61, 135, 179, 213, 227-228, 246, 268, 270, 285, 292-296, 301-302, 361-362, 382, 407-423, 452, 467, 489 |
| src/kraft/client/context.py              |       47 |        2 |     96% |     32-33 |
| src/kraft/client/reads.py                |      153 |       14 |     91% |151, 221-222, 232-239, 263-266, 289, 295, 316 |
| src/kraft/client/transport.py            |       70 |        8 |     89% |52, 67-68, 114-115, 117, 127-128 |
| src/kraft/config.py                      |      431 |       15 |     97% |100, 102, 120-122, 246, 445, 448, 547, 722-723, 768-769, 790-791 |
| src/kraft/db.py                          |      111 |        0 |    100% |           |
| src/kraft/doctor.py                      |      332 |       27 |     92% |107, 147-148, 165-166, 176-178, 190, 194-195, 234, 298-299, 333-340, 407-408, 501, 513-514, 557, 590, 598, 644, 648-649 |
| src/kraft/escalate.py                    |      145 |        5 |     97% |158, 338, 443-444, 482 |
| src/kraft/events.py                      |       17 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py       |        9 |        0 |    100% |           |
| src/kraft/executor/context.py            |       53 |        2 |     96% |  200, 231 |
| src/kraft/executor/dispatch.py           |      639 |       16 |     97% |215, 252, 319, 370, 385, 479, 490, 493, 524-525, 874-875, 1178, 1824, 2176, 2182 |
| src/kraft/executor/entry.py              |      122 |        1 |     99% |       313 |
| src/kraft/executor/fallback.py           |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py              |      285 |        7 |     98% |94, 460, 593, 675, 728-729, 1013 |
| src/kraft/executor/prompts.py            |      179 |        6 |     97% |142-148, 235, 622 |
| src/kraft/executor/read\_only.py         |       75 |        2 |     97% |    70, 83 |
| src/kraft/executor/resuming.py           |       79 |        1 |     99% |       149 |
| src/kraft/executor/retry.py              |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py              |      132 |        1 |     99% |       347 |
| src/kraft/executor/walk.py               |      514 |       13 |     97% |530, 634, 905, 927, 935, 1008-1023, 1068, 1282, 1443, 1570, 1621, 1667 |
| src/kraft/findings.py                    |      108 |        0 |    100% |           |
| src/kraft/gate\_review.py                |       46 |        1 |     98% |       123 |
| src/kraft/harness.py                     |      222 |       14 |     94% |131, 139, 154, 159, 176, 183, 190, 202, 235, 324-325, 362-364 |
| src/kraft/index/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                 |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                    |       65 |        5 |     92% |149-150, 159-161 |
| src/kraft/index/embed.py                 |       58 |       15 |     74% |34-38, 62, 67-73, 93-95 |
| src/kraft/index/ingest.py                |      209 |       11 |     95% |77-78, 142-143, 233, 323-325, 435-437 |
| src/kraft/index/service.py               |      363 |       34 |     91% |140-142, 193-195, 229-231, 267, 286-288, 341-342, 347, 350-351, 450, 472, 475-476, 495-496, 512, 530, 534-535, 701, 704, 707, 711-712, 714 |
| src/kraft/init.py                        |       51 |        4 |     92% |62-64, 125-126 |
| src/kraft/intake.py                      |      101 |       14 |     86% |59, 72-74, 87, 90, 125-126, 160-162, 181-186, 206 |
| src/kraft/intent.py                      |      130 |        9 |     93% |91-92, 172-186, 203, 234 |
| src/kraft/logs.py                        |      175 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 166-167, 175-176, 276 |
| src/kraft/mcp.py                         |       79 |       20 |     75% |30, 37, 42, 49, 147, 154, 161, 173, 187, 198, 207, 214, 223, 231, 239, 253, 268, 333, 342-343 |
| src/kraft/notify.py                      |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                   |       73 |        3 |     96% |55, 128-129 |
| src/kraft/paths.py                       |       46 |        0 |    100% |           |
| src/kraft/policy.py                      |      496 |       18 |     96% |673, 810, 1023-1032, 1112, 1119, 1133, 1137-1141, 1146 |
| src/kraft/progress.py                    |       99 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py          |       58 |        9 |     84% |155-157, 165-170 |
| src/kraft/render.py                      |      190 |       15 |     92% |46-47, 76, 83, 344-351, 379, 391, 417 |
| src/kraft/review.py                      |       45 |        1 |     98% |        73 |
| src/kraft/skill.py                       |       45 |        3 |     93% |94, 118-119 |
| src/kraft/store/\_\_init\_\_.py          |       11 |        0 |    100% |           |
| src/kraft/store/\_common.py              |       24 |        1 |     96% |        77 |
| src/kraft/store/budget.py                |       62 |        0 |    100% |           |
| src/kraft/store/chain.py                 |      183 |        5 |     97% |321, 409-411, 715 |
| src/kraft/store/counters.py              |       45 |        0 |    100% |           |
| src/kraft/store/forks.py                 |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                 |       47 |        0 |    100% |           |
| src/kraft/store/repos.py                 |       19 |        0 |    100% |           |
| src/kraft/store/sessions.py              |      117 |        0 |    100% |           |
| src/kraft/store/work\_items.py           |      154 |        0 |    100% |           |
| src/kraft/templates/\_\_init\_\_.py      |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py         |       25 |        0 |    100% |           |
| src/kraft/templates/environment.py       |      247 |        2 |     99% |  468, 470 |
| src/kraft/templates/forks.py             |      100 |        0 |    100% |           |
| src/kraft/templates/library.py           |      334 |        8 |     98% |160, 162, 174, 181, 268-269, 513, 534 |
| src/kraft/templates/models.py            |      687 |        9 |     99% |97, 100, 482, 572, 659, 1232-1233, 1495, 1706 |
| src/kraft/templates/retry.py             |      101 |       11 |     89% |183, 185-191, 193-194, 201 |
| src/kraft/templates/revision.py          |      241 |        6 |     98% |216, 249, 298, 328, 450, 484 |
| src/kraft/triggers.py                    |       46 |        6 |     87% |59-60, 91-94 |
| src/kraft/update.py                      |      119 |       10 |     92% |53-54, 84-88, 93, 109, 144-146 |
| src/kraft/usage.py                       |      306 |       13 |     96% |113, 222, 232, 331, 386, 406-407, 410-411, 423, 498, 518-519 |
| src/kraft/waits.py                       |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/worker/env.py                  |        9 |        0 |    100% |           |
| src/kraft/worker/reattach.py             |      192 |       20 |     90% |59-60, 68-69, 102-103, 194, 239-249, 280, 447, 453-454 |
| src/kraft/worker/sandbox.py              |      126 |        9 |     93% |220, 241-242, 267-268, 283-284, 522-523 |
| src/kraft/worker/steering.py             |       93 |        8 |     91% |135-136, 147-149, 175-176, 186 |
| src/kraft/worker/worktree\_read.py       |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                          |       61 |        0 |    100% |           |
| **TOTAL**                                | **16574** |  **835** | **95%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/itsOmidKarami/kraft/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/itsOmidKarami/kraft/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FitsOmidKarami%2Fkraft%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.