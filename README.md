# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                    |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py                  |      252 |       11 |     96% |331, 385-386, 388, 561, 665-668, 880-881 |
| src/kraft/adapters/artifact\_notes.py        |        1 |        0 |    100% |           |
| src/kraft/adapters/beads.py                  |       62 |       28 |     55% |40-45, 49, 82-88, 122, 125-126, 178-190 |
| src/kraft/adapters/forge/\_\_init\_\_.py     |        8 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py               |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/gh.py               |      152 |       14 |     91% |172, 252, 276, 282-292, 314-315, 370, 421 |
| src/kraft/adapters/forge/git.py              |      104 |        4 |     96% |225-232, 369 |
| src/kraft/adapters/forge/glab.py             |      176 |        9 |     95% |239-240, 327, 359-360, 426, 429, 442, 464 |
| src/kraft/adapters/forge/models.py           |      156 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py               |      124 |        5 |     96% |74, 197, 249, 315-316 |
| src/kraft/adapters/forge/run.py              |      436 |       11 |     97% |401, 585-587, 828-829, 859-870, 1362, 1455 |
| src/kraft/adapters/hook\_install.py          |      149 |        5 |     97% |110-113, 220 |
| src/kraft/adapters/profiles.py               |       33 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      377 |       11 |     97% |242-243, 257-258, 285, 468-469, 809, 835-836, 1005 |
| src/kraft/analytics.py                       |      256 |        7 |     97% |52-53, 181, 187, 438-440 |
| src/kraft/api/\_\_init\_\_.py                |       30 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      258 |       26 |     90% |105, 128, 143-144, 161, 194-195, 205-206, 227, 257, 279, 299, 318, 330-331, 348-349, 366, 404-405, 420-421, 423, 429, 431 |
| src/kraft/api/deps.py                        |      286 |       21 |     93% |83-84, 211-212, 328, 331-333, 386-387, 423-424, 433-434, 567-568, 603, 606, 609, 632-633 |
| src/kraft/api/perimeter.py                   |       70 |        0 |    100% |           |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py            |      153 |       11 |     93% |142, 162, 165, 170-171, 188-195, 316 |
| src/kraft/api/routes/auth.py                 |       45 |        6 |     87% |     54-61 |
| src/kraft/api/routes/board.py                |      129 |        1 |     99% |       173 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/gates.py                |      131 |        9 |     93% |109, 112, 120, 297, 319, 352-353, 378-379 |
| src/kraft/api/routes/harnesses.py            |       86 |        1 |     99% |       204 |
| src/kraft/api/routes/lifecycle.py            |      596 |       33 |     94% |143, 146-147, 152-153, 242, 272, 288, 411, 506-507, 517, 662-663, 715, 758, 763, 794, 803, 862, 957-958, 1062, 1147, 1177-1178, 1241, 1261-1262, 1382-1383, 1422, 1432 |
| src/kraft/api/routes/repos.py                |      168 |        6 |     96% |64-68, 97, 116, 254-255 |
| src/kraft/api/routes/review.py               |      291 |       40 |     86% |46, 51, 83, 108, 142-145, 148, 150, 160, 171-172, 185-190, 195-211, 216-219, 227, 336-337, 382-383, 452, 484 |
| src/kraft/api/routes/search.py               |      117 |       14 |     88% |47-48, 74-77, 80, 107-111, 134, 140-141, 171 |
| src/kraft/api/routes/sessions.py             |      179 |       11 |     94% |96, 142, 146, 280, 288, 367-369, 373, 375, 378 |
| src/kraft/api/routes/settings.py             |      349 |       24 |     93% |76-77, 116-117, 178-179, 233, 275-276, 391-394, 397, 441-444, 447-448, 452-457, 514, 516 |
| src/kraft/api/routes/work\_items.py          |      290 |       14 |     95% |131, 306-309, 419-420, 583, 600, 617-618, 703-704, 756-757, 762 |
| src/kraft/api/startup.py                     |      143 |        4 |     97% |94-96, 153-154 |
| src/kraft/archive.py                         |       29 |        6 |     79% |     52-57 |
| src/kraft/auth.py                            |       74 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       48 |        8 |     83% |140-148, 181-186 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      394 |       18 |     95% |106-107, 115, 199, 265, 356-358, 399-400, 414-415, 713, 729, 830-831, 1306-1309 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       22 |        0 |    100% |           |
| src/kraft/caps.py                            |      269 |       17 |     94% |291, 317, 328, 340-341, 354, 451, 523, 568-570, 608-613 |
| src/kraft/cli/\_\_init\_\_.py                |       39 |        1 |     97% |       118 |
| src/kraft/cli/admin.py                       |      473 |       39 |     92% |72, 155-157, 175, 181-185, 194, 279, 306, 347, 394-395, 433-434, 464, 555, 644, 682-684, 714-715, 719-723, 732-738, 921, 940 |
| src/kraft/cli/common.py                      |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      239 |        9 |     96% |30, 116, 124, 180, 184, 200, 237-238, 281 |
| src/kraft/cli/repo.py                        |       67 |        4 |     94% |69-70, 82-83 |
| src/kraft/cli/templates.py                   |       73 |        2 |     97% |   90, 101 |
| src/kraft/cli/view.py                        |      223 |       14 |     94% |72, 93-97, 179-180, 204, 214, 260-261, 263-269, 286-287 |
| src/kraft/client/\_\_init\_\_.py             |        4 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      171 |       33 |     81% |61, 135, 179, 213, 227-228, 246, 268, 270, 285, 292-296, 301-302, 319-320, 369-370, 390, 415-431, 460, 475, 497, 540, 545 |
| src/kraft/client/context.py                  |       47 |        2 |     96% |     32-33 |
| src/kraft/client/reads.py                    |      162 |       16 |     90% |151, 221-222, 232-239, 297-300, 323, 329, 348-349, 370 |
| src/kraft/client/transport.py                |       70 |        8 |     89% |52, 67-68, 114-115, 117, 128-129 |
| src/kraft/config.py                          |      446 |       15 |     97% |102, 120-122, 246, 445, 448, 547, 722-723, 768-769, 790-791, 936 |
| src/kraft/config\_schemas.py                 |       73 |        7 |     90% |   160-166 |
| src/kraft/db.py                              |      112 |        0 |    100% |           |
| src/kraft/doctor.py                          |      413 |       31 |     92% |111, 151-152, 169-170, 180-182, 194, 198-199, 238, 302-303, 337-344, 411-412, 505, 517-518, 598-599, 670, 719, 727, 790-791, 816, 820-821 |
| src/kraft/escalate.py                        |      179 |        6 |     97% |178, 281, 396, 525-526, 565 |
| src/kraft/events.py                          |       17 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |        9 |        0 |    100% |           |
| src/kraft/executor/context.py                |       54 |        2 |     96% |  215, 246 |
| src/kraft/executor/dispatch.py               |      644 |       17 |     97% |215, 252, 319, 370, 385, 479, 490, 493, 524-525, 875-876, 1184, 1192, 1842, 2194, 2200 |
| src/kraft/executor/entry.py                  |      122 |        1 |     99% |       313 |
| src/kraft/executor/fallback.py               |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      326 |       12 |     96% |95, 222, 337, 340, 343, 346, 583, 716, 798, 851-852, 1139 |
| src/kraft/executor/prompts.py                |      197 |        6 |     97% |144-150, 237, 670 |
| src/kraft/executor/read\_only.py             |       75 |        2 |     97% |    70, 83 |
| src/kraft/executor/resuming.py               |       80 |        1 |     99% |       155 |
| src/kraft/executor/retry.py                  |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py                  |      132 |        1 |     99% |       347 |
| src/kraft/executor/walk.py                   |      534 |       13 |     98% |535, 639, 910, 932, 940, 1013-1028, 1073, 1297, 1460, 1587, 1638, 1684 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       68 |        4 |     94% |102-105, 110, 184 |
| src/kraft/grants.py                          |       55 |        0 |    100% |           |
| src/kraft/harness.py                         |      330 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 397, 549, 551, 553, 562, 581-582, 619-621 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       67 |        5 |     93% |149-150, 159-161 |
| src/kraft/index/embed.py                     |       58 |       15 |     74% |34-38, 62, 67-73, 93-95 |
| src/kraft/index/ingest.py                    |      209 |       11 |     95% |77-78, 142-143, 233, 323-325, 435-437 |
| src/kraft/index/service.py                   |      363 |       34 |     91% |140-142, 193-195, 229-231, 267, 286-288, 341-342, 347, 350-351, 450, 472, 475-476, 495-496, 512, 530, 534-535, 701, 704, 707, 711-712, 714 |
| src/kraft/init.py                            |       51 |        4 |     92% |62-64, 125-126 |
| src/kraft/intake.py                          |      101 |       14 |     86% |59, 72-74, 87, 90, 125-126, 160-162, 181-186, 206 |
| src/kraft/intent.py                          |      130 |        9 |     93% |91-92, 172-186, 203, 234 |
| src/kraft/logs.py                            |      175 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 166-167, 175-176, 276 |
| src/kraft/mcp.py                             |      100 |       21 |     79% |30, 37, 42, 49, 147, 154, 161, 168, 180, 194, 205, 214, 221, 230, 238, 246, 260, 275, 406, 415-416 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                       |       73 |        3 |     96% |55, 128-129 |
| src/kraft/paths.py                           |       52 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       69 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/policy.py                          |      661 |       20 |     97% |251-252, 1011, 1167, 1401-1410, 1490, 1497, 1511, 1515-1519, 1524 |
| src/kraft/progress.py                        |      113 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       58 |        9 |     84% |155-157, 165-170 |
| src/kraft/render.py                          |      220 |       23 |     90% |46-47, 76, 83, 312, 329, 342, 348-352, 390-397, 425, 437, 463 |
| src/kraft/review.py                          |      111 |        6 |     95% |74, 204, 208, 242, 244, 256 |
| src/kraft/review\_reply.py                   |       75 |        7 |     91% |75, 93-94, 122-123, 128, 142 |
| src/kraft/skill.py                           |       45 |        3 |     93% |94, 118-119 |
| src/kraft/store/\_\_init\_\_.py              |       12 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       24 |        1 |     96% |        77 |
| src/kraft/store/budget.py                    |       65 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      183 |        5 |     97% |321, 409-411, 715 |
| src/kraft/store/counters.py                  |       45 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       47 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      173 |        6 |     97% |194, 199, 206-207, 221, 228 |
| src/kraft/store/sessions.py                  |      131 |        0 |    100% |           |
| src/kraft/store/work\_items.py               |      157 |        0 |    100% |           |
| src/kraft/templates/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py             |       25 |        0 |    100% |           |
| src/kraft/templates/environment.py           |      247 |        2 |     99% |  468, 470 |
| src/kraft/templates/forks.py                 |      100 |        0 |    100% |           |
| src/kraft/templates/library.py               |      359 |        5 |     99% |192, 194, 206, 213, 574 |
| src/kraft/templates/models.py                |      687 |        9 |     99% |97, 100, 482, 572, 659, 1232-1233, 1495, 1706 |
| src/kraft/templates/positions.py             |       57 |        1 |     98% |        54 |
| src/kraft/templates/retry.py                 |      103 |       11 |     89% |188, 190-196, 198-199, 206 |
| src/kraft/templates/revision.py              |      250 |        9 |     96% |216, 249, 298, 328, 450, 491-492, 494, 508 |
| src/kraft/triggers.py                        |       52 |        6 |     88% |61-62, 101-104 |
| src/kraft/update.py                          |      135 |        9 |     93% |76-77, 107-111, 116, 177-179 |
| src/kraft/usage.py                           |      408 |       19 |     95% |109-110, 204, 335, 453, 513, 533-534, 537-538, 578, 656, 676-677, 851, 923-924, 945-946 |
| src/kraft/waits.py                           |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      608 |       44 |     93% |281, 293-296, 302-303, 351, 420-421, 448, 451-454, 521, 709-710, 819-820, 847, 944-945, 993, 1036-1039, 1115-1116, 1138, 1141-1142, 1185, 1239-1240, 1270-1271, 1347-1348, 1384-1385, 1461-1462, 1482-1483 |
| src/kraft/worker/backends/docker\_forward.py |      134 |        7 |     95% |92-93, 95, 212-214, 222 |
| src/kraft/worker/ca.py                       |      104 |        1 |     99% |       289 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      159 |        7 |     96% |141, 146, 177-178, 279, 305-306 |
| src/kraft/worker/egress.py                   |      454 |       36 |     92% |289-290, 296, 337-339, 358, 394, 400, 416-417, 427, 445, 447, 507, 512, 557, 565-566, 666, 677, 692, 716-717, 719-720, 732, 735, 765-766, 816-821 |
| src/kraft/worker/env.py                      |        9 |        0 |    100% |           |
| src/kraft/worker/inject.py                   |      234 |       18 |     92% |178, 198, 214, 225, 237, 335, 349, 358, 368, 374, 377, 391, 394, 399, 408-411 |
| src/kraft/worker/reattach.py                 |      265 |       26 |     90% |70-71, 79-80, 113-114, 213, 258-268, 299, 471, 477-478, 535-536, 549-550, 563-564 |
| src/kraft/worker/refstore.py                 |      189 |       11 |     94% |124-125, 130-131, 152, 200, 203, 256, 301-302, 325 |
| src/kraft/worker/sandbox.py                  |       98 |        1 |     99% |       125 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |44, 49-50, 52 |
| src/kraft/worker/shim.py                     |        4 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       94 |        8 |     91% |152-153, 164-166, 192-193, 203 |
| src/kraft/worker/worktree\_read.py           |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                              |       61 |        0 |    100% |           |
| **TOTAL**                                    | **21269** | **1125** | **95%** |           |


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