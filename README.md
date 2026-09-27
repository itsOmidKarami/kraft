# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                     |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py       |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py              |      204 |        5 |     98% |327, 381-382, 384, 557 |
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
| src/kraft/adapters/hook\_install.py      |       95 |        1 |     99% |       140 |
| src/kraft/adapters/profiles.py           |       33 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py         |      271 |       10 |     96% |235-236, 250-251, 278, 423-424, 601-602, 752 |
| src/kraft/analytics.py                   |      256 |        7 |     97% |52-53, 181, 187, 438-440 |
| src/kraft/api/\_\_init\_\_.py            |       30 |        0 |    100% |           |
| src/kraft/api/config\_check.py           |      258 |       26 |     90% |105, 128, 143-144, 161, 194-195, 205-206, 227, 257, 279, 299, 318, 330-331, 348-349, 366, 404-405, 420-421, 423, 429, 431 |
| src/kraft/api/deps.py                    |      286 |       21 |     93% |83-84, 211-212, 328, 331-333, 386-387, 423-424, 433-434, 567-568, 603, 606, 609, 632-633 |
| src/kraft/api/perimeter.py               |       70 |        0 |    100% |           |
| src/kraft/api/routes/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py        |      153 |       11 |     93% |142, 162, 165, 170-171, 188-195, 316 |
| src/kraft/api/routes/auth.py             |       45 |        6 |     87% |     54-61 |
| src/kraft/api/routes/board.py            |      129 |        1 |     99% |       173 |
| src/kraft/api/routes/check.py            |       20 |        0 |    100% |           |
| src/kraft/api/routes/gates.py            |      131 |        9 |     93% |109, 112, 120, 297, 319, 352-353, 378-379 |
| src/kraft/api/routes/harnesses.py        |       86 |        1 |     99% |       204 |
| src/kraft/api/routes/lifecycle.py        |      590 |       34 |     94% |142, 145-146, 151-152, 233, 262, 277, 395, 400, 495-496, 506, 647-648, 700, 743, 748, 779, 788, 847, 939-940, 1044, 1129, 1159-1160, 1223, 1243-1244, 1364-1365, 1404, 1414 |
| src/kraft/api/routes/repos.py            |      168 |        6 |     96% |64-68, 97, 116, 254-255 |
| src/kraft/api/routes/review.py           |      288 |       48 |     83% |46, 51, 83, 108, 142-145, 148, 150, 160, 165-181, 185-190, 195-211, 216-219, 227, 333-334, 379-380, 449, 481 |
| src/kraft/api/routes/search.py           |      117 |       14 |     88% |47-48, 74-77, 80, 107-111, 134, 140-141, 171 |
| src/kraft/api/routes/sessions.py         |      157 |        9 |     94% |95, 141, 145, 317-319, 323, 325, 328 |
| src/kraft/api/routes/settings.py         |      349 |       24 |     93% |76-77, 116-117, 178-179, 233, 275-276, 391-394, 397, 441-444, 447-448, 452-457, 514, 516 |
| src/kraft/api/routes/work\_items.py      |      290 |       14 |     95% |131, 306-309, 419-420, 583, 600, 617-618, 703-704, 756-757, 762 |
| src/kraft/api/startup.py                 |      130 |        4 |     97% |79-81, 138-139 |
| src/kraft/archive.py                     |       29 |        6 |     79% |     52-57 |
| src/kraft/auth.py                        |       74 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py       |       48 |        8 |     83% |140-148, 181-186 |
| src/kraft/automated\_review.py           |       12 |        0 |    100% |           |
| src/kraft/builtins.py                    |      351 |       17 |     95% |98-99, 107, 191, 257, 348-350, 391-392, 406-407, 665, 750-751, 1205-1208 |
| src/kraft/cap\_levels.py                 |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                |       22 |        0 |    100% |           |
| src/kraft/caps.py                        |      269 |       17 |     94% |291, 317, 328, 340-341, 354, 451, 523, 568-570, 608-613 |
| src/kraft/cli/\_\_init\_\_.py            |       39 |        1 |     97% |       118 |
| src/kraft/cli/admin.py                   |      473 |       39 |     92% |72, 155-157, 175, 181-185, 194, 279, 306, 347, 394-395, 433-434, 464, 555, 644, 682-684, 714-715, 719-723, 732-738, 921, 940 |
| src/kraft/cli/common.py                  |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                    |      239 |        9 |     96% |30, 116, 124, 180, 184, 200, 237-238, 281 |
| src/kraft/cli/repo.py                    |       67 |        4 |     94% |69-70, 82-83 |
| src/kraft/cli/templates.py               |       73 |        2 |     97% |   90, 101 |
| src/kraft/cli/view.py                    |      223 |       14 |     94% |72, 93-97, 179-180, 204, 214, 260-261, 263-269, 286-287 |
| src/kraft/client/\_\_init\_\_.py         |        4 |        0 |    100% |           |
| src/kraft/client/actions.py              |      171 |       44 |     74% |61, 135, 179, 213, 227-228, 246, 268, 270, 285, 292-296, 301-302, 319-320, 369-370, 390, 415-431, 460, 475, 497, 520-521, 523-533, 540, 545, 557-558 |
| src/kraft/client/context.py              |       47 |        2 |     96% |     32-33 |
| src/kraft/client/reads.py                |      162 |       19 |     88% |151, 221-222, 232-239, 297-300, 323, 329, 336-338, 348-349, 370 |
| src/kraft/client/transport.py            |       70 |        8 |     89% |52, 67-68, 114-115, 117, 128-129 |
| src/kraft/config.py                      |      431 |       15 |     97% |100, 102, 120-122, 246, 445, 448, 547, 722-723, 768-769, 790-791 |
| src/kraft/config\_schemas.py             |       73 |        7 |     90% |   159-165 |
| src/kraft/db.py                          |      112 |        0 |    100% |           |
| src/kraft/doctor.py                      |      350 |       29 |     92% |108, 148-149, 166-167, 177-179, 191, 195-196, 235, 299-300, 334-341, 408-409, 502, 514-515, 558, 597, 605, 655-656, 681, 685-686 |
| src/kraft/escalate.py                    |      173 |        6 |     97% |161, 264, 379, 509-510, 549 |
| src/kraft/events.py                      |       17 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py       |        9 |        0 |    100% |           |
| src/kraft/executor/context.py            |       54 |        2 |     96% |  215, 246 |
| src/kraft/executor/dispatch.py           |      644 |       17 |     97% |215, 252, 319, 370, 385, 479, 490, 493, 524-525, 875-876, 1184, 1192, 1842, 2194, 2200 |
| src/kraft/executor/entry.py              |      122 |        1 |     99% |       313 |
| src/kraft/executor/fallback.py           |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py              |      326 |       12 |     96% |95, 222, 337, 340, 343, 346, 583, 716, 798, 851-852, 1136 |
| src/kraft/executor/prompts.py            |      191 |        6 |     97% |143-149, 236, 655 |
| src/kraft/executor/read\_only.py         |       75 |        2 |     97% |    70, 83 |
| src/kraft/executor/resuming.py           |       80 |        1 |     99% |       155 |
| src/kraft/executor/retry.py              |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py              |      132 |        1 |     99% |       347 |
| src/kraft/executor/walk.py               |      532 |       13 |     98% |530, 634, 905, 927, 935, 1008-1023, 1068, 1292, 1455, 1582, 1633, 1679 |
| src/kraft/findings.py                    |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                |       68 |        4 |     94% |102-105, 110, 184 |
| src/kraft/grants.py                      |       55 |        0 |    100% |           |
| src/kraft/harness.py                     |      268 |       17 |     94% |162, 170, 185, 206, 226, 233, 240, 252, 323, 412, 414, 416, 435-436, 473-475 |
| src/kraft/index/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                 |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                    |       67 |        5 |     93% |149-150, 159-161 |
| src/kraft/index/embed.py                 |       58 |       15 |     74% |34-38, 62, 67-73, 93-95 |
| src/kraft/index/ingest.py                |      209 |       11 |     95% |77-78, 142-143, 233, 323-325, 435-437 |
| src/kraft/index/service.py               |      363 |       34 |     91% |140-142, 193-195, 229-231, 267, 286-288, 341-342, 347, 350-351, 450, 472, 475-476, 495-496, 512, 530, 534-535, 701, 704, 707, 711-712, 714 |
| src/kraft/init.py                        |       51 |        4 |     92% |62-64, 125-126 |
| src/kraft/intake.py                      |      101 |       14 |     86% |59, 72-74, 87, 90, 125-126, 160-162, 181-186, 206 |
| src/kraft/intent.py                      |      130 |        9 |     93% |91-92, 172-186, 203, 234 |
| src/kraft/logs.py                        |      175 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 166-167, 175-176, 276 |
| src/kraft/mcp.py                         |      100 |       21 |     79% |30, 37, 42, 49, 147, 154, 161, 168, 180, 194, 205, 214, 221, 230, 238, 246, 260, 275, 406, 415-416 |
| src/kraft/node\_runs.py                  |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                      |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                   |       73 |        3 |     96% |55, 128-129 |
| src/kraft/paths.py                       |       46 |        0 |    100% |           |
| src/kraft/permission\_hooks.py           |       65 |        1 |     98% |        39 |
| src/kraft/permission\_rules.py           |       37 |        0 |    100% |           |
| src/kraft/policy.py                      |      535 |       20 |     96% |249-250, 734, 890, 1124-1133, 1213, 1220, 1234, 1238-1242, 1247 |
| src/kraft/progress.py                    |      113 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py          |       58 |        9 |     84% |155-157, 165-170 |
| src/kraft/render.py                      |      220 |       23 |     90% |46-47, 76, 83, 312, 329, 342, 348-352, 390-397, 425, 437, 463 |
| src/kraft/review.py                      |      111 |        6 |     95% |74, 204, 208, 242, 244, 256 |
| src/kraft/review\_reply.py               |       62 |        5 |     92% |74, 102-103, 106, 120 |
| src/kraft/skill.py                       |       45 |        3 |     93% |94, 118-119 |
| src/kraft/store/\_\_init\_\_.py          |       12 |        0 |    100% |           |
| src/kraft/store/\_common.py              |       24 |        1 |     96% |        77 |
| src/kraft/store/budget.py                |       65 |        0 |    100% |           |
| src/kraft/store/chain.py                 |      183 |        5 |     97% |321, 409-411, 715 |
| src/kraft/store/counters.py              |       45 |        0 |    100% |           |
| src/kraft/store/forks.py                 |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                 |       47 |        0 |    100% |           |
| src/kraft/store/repos.py                 |       19 |        0 |    100% |           |
| src/kraft/store/review.py                |      173 |        6 |     97% |194, 199, 206-207, 221, 228 |
| src/kraft/store/sessions.py              |      128 |        0 |    100% |           |
| src/kraft/store/work\_items.py           |      154 |        0 |    100% |           |
| src/kraft/templates/\_\_init\_\_.py      |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py         |       25 |        0 |    100% |           |
| src/kraft/templates/environment.py       |      247 |        2 |     99% |  468, 470 |
| src/kraft/templates/forks.py             |      100 |        0 |    100% |           |
| src/kraft/templates/library.py           |      359 |        5 |     99% |192, 194, 206, 213, 574 |
| src/kraft/templates/models.py            |      687 |        9 |     99% |97, 100, 482, 572, 659, 1232-1233, 1495, 1706 |
| src/kraft/templates/positions.py         |       57 |        1 |     98% |        54 |
| src/kraft/templates/retry.py             |      103 |       11 |     89% |188, 190-196, 198-199, 206 |
| src/kraft/templates/revision.py          |      250 |        9 |     96% |216, 249, 298, 328, 450, 491-492, 494, 508 |
| src/kraft/triggers.py                    |       52 |        6 |     88% |61-62, 101-104 |
| src/kraft/update.py                      |      135 |        9 |     93% |76-77, 107-111, 116, 177-179 |
| src/kraft/usage.py                       |      408 |       19 |     95% |109-110, 204, 335, 453, 513, 533-534, 537-538, 578, 656, 676-677, 851, 923-924, 945-946 |
| src/kraft/waits.py                       |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/worker/env.py                  |        9 |        0 |    100% |           |
| src/kraft/worker/reattach.py             |      198 |       20 |     90% |61-62, 70-71, 104-105, 204, 249-259, 290, 454, 460-461 |
| src/kraft/worker/sandbox.py              |      126 |        9 |     93% |220, 241-242, 267-268, 283-284, 522-523 |
| src/kraft/worker/steering.py             |       94 |        8 |     91% |152-153, 164-166, 192-193, 203 |
| src/kraft/worker/worktree\_read.py       |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                          |       61 |        0 |    100% |           |
| **TOTAL**                                | **18646** | **1002** | **95%** |           |


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