# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                    |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py                  |      258 |       11 |     96% |332, 386-387, 389, 562, 666-669, 883-884 |
| src/kraft/adapters/artifact\_notes.py        |        2 |        0 |    100% |           |
| src/kraft/adapters/beads.py                  |       62 |       28 |     55% |40-45, 49, 82-88, 122, 125-126, 178-190 |
| src/kraft/adapters/forge/\_\_init\_\_.py     |        8 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py               |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/gh.py               |      152 |       14 |     91% |172, 252, 276, 282-292, 314-315, 370, 421 |
| src/kraft/adapters/forge/git.py              |      133 |        4 |     97% |47, 296-297, 475 |
| src/kraft/adapters/forge/glab.py             |      176 |        9 |     95% |239-240, 327, 359-360, 426, 429, 442, 464 |
| src/kraft/adapters/forge/models.py           |      156 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py               |      124 |        5 |     96% |74, 197, 249, 315-316 |
| src/kraft/adapters/forge/run.py              |      449 |       11 |     98% |423, 607-609, 852-853, 883-894, 1396, 1490 |
| src/kraft/adapters/hook\_install.py          |      149 |        5 |     97% |110-113, 220 |
| src/kraft/adapters/profiles.py               |       33 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      405 |       12 |     97% |243-244, 258-259, 286, 487-488, 552, 877, 903-904, 1073 |
| src/kraft/analytics.py                       |      256 |        7 |     97% |52-53, 182, 188, 440-442 |
| src/kraft/api/\_\_init\_\_.py                |       30 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      274 |       24 |     91% |105, 128, 143-144, 161, 194-195, 205-206, 227, 257, 279, 299, 318, 348-349, 366, 440-441, 456-457, 459, 465, 467 |
| src/kraft/api/deps.py                        |      308 |       21 |     93% |83-84, 211-212, 389, 392-394, 466-467, 505-506, 515-516, 649-650, 685, 688, 691, 714-715 |
| src/kraft/api/perimeter.py                   |       77 |        0 |    100% |           |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py            |      153 |       11 |     93% |142, 162, 165, 170-171, 188-195, 316 |
| src/kraft/api/routes/auth.py                 |       57 |        6 |     89% |     81-88 |
| src/kraft/api/routes/board.py                |      129 |        1 |     99% |       173 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/gates.py                |      133 |        9 |     93% |109, 112, 120, 299, 321, 354-355, 380-381 |
| src/kraft/api/routes/harnesses.py            |       86 |        1 |     99% |       204 |
| src/kraft/api/routes/lifecycle.py            |      628 |       35 |     94% |144, 147-148, 153-154, 239-240, 262, 293, 325, 343, 467, 563-564, 574, 719-720, 773, 816, 821, 852, 861, 920, 1015-1016, 1241, 1271-1272, 1335, 1355-1356, 1478-1479, 1518, 1528 |
| src/kraft/api/routes/repos.py                |      180 |        6 |     97% |85-89, 126, 145, 282-283 |
| src/kraft/api/routes/review.py               |      291 |       40 |     86% |46, 51, 83, 108, 142-145, 148, 150, 160, 171-172, 185-190, 195-211, 216-219, 227, 336-337, 382-383, 452, 484 |
| src/kraft/api/routes/search.py               |      117 |       14 |     88% |47-48, 74-77, 80, 107-111, 134, 140-141, 171 |
| src/kraft/api/routes/sessions.py             |      180 |       11 |     94% |96, 142, 146, 285, 293, 372-374, 378, 380, 383 |
| src/kraft/api/routes/settings.py             |      348 |       24 |     93% |76-77, 116-117, 173-174, 228, 270-271, 386-389, 392, 436-439, 442-443, 447-452, 509, 511 |
| src/kraft/api/routes/work\_items.py          |      291 |       14 |     95% |134, 308-311, 420-421, 584, 601, 618-619, 715-716, 768-769, 774 |
| src/kraft/api/startup.py                     |      146 |        4 |     97% |97-99, 156-157 |
| src/kraft/archive.py                         |       29 |        6 |     79% |     52-57 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       48 |        8 |     83% |140-148, 181-186 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      467 |       23 |     95% |107-108, 116, 207, 273, 357, 371, 405, 409, 413, 419, 490-491, 511-512, 771-772, 898, 914, 1016-1017, 1534-1537 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      269 |       17 |     94% |291, 317, 328, 340-341, 354, 451, 523, 568-570, 608-613 |
| src/kraft/cli/\_\_init\_\_.py                |       39 |        1 |     97% |       118 |
| src/kraft/cli/admin.py                       |      483 |       38 |     92% |72, 155-157, 175, 181-185, 194, 279, 306, 347, 394-395, 433-434, 464, 555, 661, 708-709, 739-740, 744-748, 757-763, 946, 965 |
| src/kraft/cli/common.py                      |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      245 |        9 |     96% |30, 116, 124, 184, 188, 204, 241-242, 285 |
| src/kraft/cli/repo.py                        |       71 |        4 |     94% |69-70, 92-93 |
| src/kraft/cli/templates.py                   |       71 |        1 |     99% |        98 |
| src/kraft/cli/view.py                        |      222 |       14 |     94% |84, 105-109, 191-192, 214, 224, 270-271, 273-279, 296-297 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      174 |       33 |     81% |63, 137, 181, 215, 229-230, 248, 270, 272, 295, 302-306, 311-312, 329-330, 379-380, 400, 425-441, 470, 485, 507, 550, 555 |
| src/kraft/client/context.py                  |       49 |        2 |     96% |     44-45 |
| src/kraft/client/reads.py                    |      160 |       16 |     90% |151, 221-222, 232-239, 297-300, 323, 329, 348-349, 370 |
| src/kraft/client/transport.py                |       70 |        8 |     89% |52, 67-68, 114-115, 117, 128-129 |
| src/kraft/config.py                          |      450 |       16 |     96% |101, 119-121, 250, 449, 452, 533, 701-702, 747-748, 769-770, 799, 940 |
| src/kraft/config\_schemas.py                 |       73 |        7 |     90% |   160-166 |
| src/kraft/db.py                              |      113 |        0 |    100% |           |
| src/kraft/doctor.py                          |      416 |       31 |     93% |123, 166-167, 185-186, 196-198, 210, 214-215, 254, 317-318, 355-362, 429-430, 527, 539-540, 620-621, 692, 734, 742, 821-822, 847, 851-852 |
| src/kraft/escalate.py                        |      180 |        6 |     97% |188, 291, 406, 536-537, 577 |
| src/kraft/events.py                          |       17 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       54 |        2 |     96% |  215, 246 |
| src/kraft/executor/dispatch.py               |      656 |       17 |     97% |215, 252, 319, 370, 394, 490, 501, 504, 535-536, 895-896, 1217, 1225, 1875, 2227, 2233 |
| src/kraft/executor/entry.py                  |      122 |        1 |     99% |       313 |
| src/kraft/executor/fallback.py               |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      326 |       12 |     96% |95, 222, 337, 340, 343, 346, 583, 716, 798, 851-852, 1139 |
| src/kraft/executor/prompts.py                |      197 |        6 |     97% |144-150, 237, 670 |
| src/kraft/executor/read\_only.py             |       75 |        2 |     97% |    70, 83 |
| src/kraft/executor/resuming.py               |       80 |        1 |     99% |       155 |
| src/kraft/executor/retry.py                  |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py                  |      139 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      534 |       13 |     98% |535, 639, 910, 932, 940, 1013-1028, 1073, 1297, 1460, 1587, 1638, 1684 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       68 |        4 |     94% |102-105, 110, 184 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      330 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 397, 549, 551, 553, 562, 581-582, 619-621 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |151-152, 161-163 |
| src/kraft/index/embed.py                     |       58 |       15 |     74% |34-38, 63, 68-74, 94-96 |
| src/kraft/index/ingest.py                    |      209 |       11 |     95% |77-78, 142-143, 233, 323-325, 435-437 |
| src/kraft/index/service.py                   |      363 |       34 |     91% |140-142, 193-195, 229-231, 267, 286-288, 341-342, 347, 350-351, 450, 472, 475-476, 495-496, 512, 530, 534-535, 701, 704, 707, 711-712, 714 |
| src/kraft/init.py                            |       51 |        4 |     92% |62-64, 125-126 |
| src/kraft/intake.py                          |      101 |       14 |     86% |59, 72-74, 87, 90, 125-126, 160-162, 181-186, 206 |
| src/kraft/intent.py                          |      130 |        9 |     93% |91-92, 172-186, 203, 234 |
| src/kraft/logs.py                            |      177 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 168-169, 177-178, 278 |
| src/kraft/mcp.py                             |      103 |       22 |     79% |31, 38, 43, 50, 149, 156, 163, 170, 182, 196, 206, 217, 226, 233, 242, 250, 258, 272, 287, 422, 431-432 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                       |       73 |        3 |     96% |55, 128-129 |
| src/kraft/paths.py                           |       56 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       69 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/policy.py                          |      661 |       20 |     97% |246-247, 1006, 1162, 1396-1405, 1485, 1492, 1506, 1510-1514, 1519 |
| src/kraft/progress.py                        |      113 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       58 |        9 |     84% |155-157, 165-170 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/render.py                          |      220 |       23 |     90% |46-47, 76, 83, 312, 329, 342, 348-352, 390-397, 425, 437, 463 |
| src/kraft/review.py                          |      111 |        6 |     95% |74, 204, 208, 242, 244, 256 |
| src/kraft/review\_reply.py                   |       75 |        7 |     91% |75, 93-94, 122-123, 128, 142 |
| src/kraft/skill.py                           |       50 |        5 |     90% |77-78, 103, 127-128 |
| src/kraft/store/\_\_init\_\_.py              |       12 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       24 |        1 |     96% |        77 |
| src/kraft/store/budget.py                    |       65 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      183 |        4 |     98% |321, 409-411 |
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
| src/kraft/templates/models.py                |      673 |        8 |     99% |97, 100, 482, 572, 659, 1230-1231, 1493 |
| src/kraft/templates/positions.py             |       57 |        1 |     98% |        54 |
| src/kraft/templates/retry.py                 |      100 |       11 |     89% |183, 185-191, 193-194, 201 |
| src/kraft/templates/revision.py              |      248 |        8 |     97% |216, 249, 298, 448, 489-490, 492, 506 |
| src/kraft/triggers.py                        |       52 |        6 |     88% |61-62, 101-104 |
| src/kraft/update.py                          |      135 |        9 |     93% |76-77, 107-111, 116, 177-179 |
| src/kraft/usage.py                           |      408 |       19 |     95% |109-110, 204, 335, 453, 513, 533-534, 537-538, 578, 656, 676-677, 851, 923-924, 945-946 |
| src/kraft/waits.py                           |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      642 |       44 |     93% |282, 294-297, 303-304, 352, 421-422, 449, 452-455, 522, 720-721, 831-832, 859, 1005-1006, 1054, 1123-1126, 1202-1203, 1225, 1228-1229, 1272, 1332-1333, 1365-1366, 1444-1445, 1481-1482, 1559-1560, 1580-1581 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       301 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      159 |        7 |     96% |141, 146, 177-178, 279, 305-306 |
| src/kraft/worker/egress.py                   |      454 |       36 |     92% |289-290, 296, 337-339, 358, 394, 400, 416-417, 427, 445, 447, 507, 512, 557, 565-566, 666, 677, 692, 716-717, 719-720, 732, 735, 765-766, 816-821 |
| src/kraft/worker/env.py                      |       10 |        0 |    100% |           |
| src/kraft/worker/inject.py                   |      234 |       18 |     92% |178, 198, 214, 225, 237, 335, 349, 358, 368, 374, 377, 391, 394, 399, 408-411 |
| src/kraft/worker/reattach.py                 |      265 |       26 |     90% |70-71, 79-80, 113-114, 213, 258-268, 299, 471, 477-478, 535-536, 549-550, 563-564 |
| src/kraft/worker/refstore.py                 |      224 |        7 |     97% |187, 190, 306, 394-395, 423, 446 |
| src/kraft/worker/sandbox.py                  |      158 |        7 |     96% |109, 147-148, 153-154, 191, 216 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |44, 49-50, 52 |
| src/kraft/worker/shim.py                     |        4 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       94 |        8 |     91% |152-153, 164-166, 192-193, 203 |
| src/kraft/worker/worktree\_read.py           |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                              |       61 |        0 |    100% |           |
| **TOTAL**                                    | **21793** | **1131** | **95%** |           |


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