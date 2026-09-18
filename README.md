# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                     |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py       |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py              |      105 |        4 |     96% |242-243, 245, 479 |
| src/kraft/adapters/beads.py              |       62 |        8 |     87% |44, 83, 86-87, 125-126, 180-181 |
| src/kraft/adapters/forge/\_\_init\_\_.py |        9 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py           |       90 |        3 |     97% |74, 299-300 |
| src/kraft/adapters/forge/gh.py           |       87 |        7 |     92% |141, 160, 222, 230-233, 273 |
| src/kraft/adapters/forge/git.py          |       85 |       10 |     88% |162-169, 281-286, 296 |
| src/kraft/adapters/forge/glab.py         |      146 |        7 |     95% |224-225, 308, 340-341, 360, 382 |
| src/kraft/adapters/forge/models.py       |      100 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py           |      108 |        5 |     95% |67, 190, 242, 295-296 |
| src/kraft/adapters/forge/run.py          |      274 |       24 |     91% |304, 445, 685-686, 716-717, 952-979 |
| src/kraft/adapters/subprocess.py         |      216 |       12 |     94% |162-163, 177-178, 205, 329-330, 486-487, 516-517, 595 |
| src/kraft/analytics.py                   |      229 |        4 |     98% |50-51, 148, 154 |
| src/kraft/api/\_\_init\_\_.py            |       30 |        0 |    100% |           |
| src/kraft/api/deps.py                    |       98 |        4 |     96% |56-57, 175-176 |
| src/kraft/api/perimeter.py               |       70 |        0 |    100% |           |
| src/kraft/api/routes/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py        |       81 |        1 |     99% |       208 |
| src/kraft/api/routes/auth.py             |       45 |        6 |     87% |     54-61 |
| src/kraft/api/routes/board.py            |      105 |        1 |     99% |       126 |
| src/kraft/api/routes/gates.py            |      136 |       16 |     88% |29, 50-51, 56, 84, 102-103, 290, 292, 315, 331-332, 354-355, 379-380 |
| src/kraft/api/routes/lifecycle.py        |      475 |       38 |     92% |110, 113-114, 119-120, 199, 228, 243, 376, 447, 449, 451, 505-506, 539, 605-606, 643-644, 697, 720, 727, 758, 767, 865-866, 908-909, 964, 967, 1016, 1048-1049, 1127-1128, 1167, 1176-1177 |
| src/kraft/api/routes/repos.py            |      125 |        4 |     97% |57-61, 182-183 |
| src/kraft/api/routes/search.py           |      117 |       14 |     88% |47-48, 74-77, 80, 107-111, 134, 140-141, 171 |
| src/kraft/api/routes/sessions.py         |      106 |        7 |     93% |69, 191-193, 197, 199, 202 |
| src/kraft/api/routes/settings.py         |      369 |       25 |     93% |42, 134, 237, 239, 241, 359, 364-367, 382-383, 407-408, 410, 442-445, 448-449, 453-458, 515, 517 |
| src/kraft/api/routes/work\_items.py      |      200 |        7 |     96% |112, 221, 224, 256-259, 354-355 |
| src/kraft/api/startup.py                 |      117 |        4 |     97% |121-122, 247-248 |
| src/kraft/archive.py                     |       29 |        6 |     79% |     52-57 |
| src/kraft/auth.py                        |       74 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py       |       45 |        6 |     87% |   172-177 |
| src/kraft/builtins.py                    |      282 |       20 |     93% |60, 71-72, 80, 164, 241, 332-334, 375-376, 390-391, 533, 621-622, 822, 972-975, 1052 |
| src/kraft/capabilities.py                |       22 |        0 |    100% |           |
| src/kraft/ci\_wait.py                    |       53 |        9 |     83% |118-120, 128-133 |
| src/kraft/cli/\_\_init\_\_.py            |       39 |        1 |     97% |       118 |
| src/kraft/cli/admin.py                   |      379 |       54 |     86% |68-74, 151-163, 167-181, 262-263, 301-302, 332, 423, 511, 549-551, 581-582, 586-590, 599-605, 727 |
| src/kraft/cli/common.py                  |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                    |      108 |        5 |     95% |59, 87, 91, 97, 109 |
| src/kraft/cli/repo.py                    |       63 |        4 |     94% |69-70, 78-79 |
| src/kraft/cli/view.py                    |      173 |       11 |     94% |57-61, 103-105, 142-143, 167, 177, 227-228 |
| src/kraft/client/\_\_init\_\_.py         |        4 |        0 |    100% |           |
| src/kraft/client/actions.py              |      116 |       26 |     78% |38, 59, 62, 105, 149, 174-178, 189-190, 284-285, 306-322, 345-362 |
| src/kraft/client/context.py              |       47 |        2 |     96% |     32-33 |
| src/kraft/client/reads.py                |      128 |       14 |     89% |135, 188-189, 199-206, 230-233, 256, 262, 283 |
| src/kraft/client/transport.py            |       70 |        8 |     89% |52, 67-68, 114-115, 117, 127-128 |
| src/kraft/config.py                      |      253 |       14 |     94% |52, 70-72, 120, 151, 154, 202, 213, 269, 461-462, 481-482 |
| src/kraft/db.py                          |      108 |        0 |    100% |           |
| src/kraft/doctor.py                      |      322 |       33 |     90% |96, 98, 123-124, 142-143, 153-155, 185-186, 218, 222-223, 267-268, 297, 301-302, 394-395, 439-440, 538-539, 559-560, 583, 598, 606, 631, 635-636 |
| src/kraft/escalate.py                    |       83 |        5 |     94% |114, 151, 176-177, 288 |
| src/kraft/events.py                      |       17 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py       |        8 |        0 |    100% |           |
| src/kraft/executor/context.py            |       32 |        1 |     97% |       101 |
| src/kraft/executor/dispatch.py           |      305 |        6 |     98% |204, 526, 644, 669, 1171, 1177 |
| src/kraft/executor/entry.py              |       89 |        1 |     99% |       207 |
| src/kraft/executor/gates.py              |      241 |       17 |     93% |63-64, 95, 157, 365, 373, 387, 428, 442, 532, 585-586, 810-813, 815 |
| src/kraft/executor/prompts.py            |      167 |        2 |     99% |  179, 496 |
| src/kraft/executor/resuming.py           |       78 |       13 |     83% |57, 140, 171-174, 184-186, 222, 224, 226, 228 |
| src/kraft/executor/stops.py              |       48 |        5 |     90% |67, 98-104 |
| src/kraft/executor/walk.py               |      365 |       38 |     90% |281, 349, 351-356, 362, 388, 390, 419, 421, 430, 455, 495, 506, 508, 510, 512, 543, 558-565, 567, 571, 573, 799, 940, 942, 1023, 1087, 1092-1097, 1136, 1252, 1264 |
| src/kraft/findings.py                    |      108 |        0 |    100% |           |
| src/kraft/gate\_review.py                |       31 |        2 |     94% |    65, 88 |
| src/kraft/harness.py                     |      202 |       20 |     90% |116, 132, 135, 145, 149-150, 173, 176, 182, 185, 195, 211, 216, 233, 240, 247, 259, 304-306 |
| src/kraft/index/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                 |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                    |       65 |        5 |     92% |149-150, 159-161 |
| src/kraft/index/embed.py                 |       53 |       17 |     68% |34-38, 62, 65-73, 85-87 |
| src/kraft/index/ingest.py                |      209 |       11 |     95% |77-78, 142-143, 233, 323-325, 435-437 |
| src/kraft/index/service.py               |      363 |       35 |     90% |140-142, 193-195, 229-231, 267, 286-288, 340-341, 345-346, 349-350, 449, 471, 474-475, 494-495, 511, 529, 533-534, 700, 703, 706, 710-711, 713 |
| src/kraft/init.py                        |       51 |        4 |     92% |62-64, 125-126 |
| src/kraft/intake.py                      |       97 |       14 |     86% |58, 71-73, 88, 91, 117-118, 148-150, 170-175, 195 |
| src/kraft/intent.py                      |      130 |        9 |     93% |90-91, 171-185, 194, 228 |
| src/kraft/logs.py                        |      103 |        9 |     91% |60-61, 102, 107, 124, 134, 151, 166-167 |
| src/kraft/mcp.py                         |       67 |       21 |     69% |30, 37, 42, 49, 78, 85, 93, 107, 114, 121, 128, 135, 143, 152, 160, 168, 183, 202, 220, 229-230 |
| src/kraft/notify.py                      |      152 |       10 |     93% |186-187, 291-294, 319-322 |
| src/kraft/paths.py                       |       46 |        0 |    100% |           |
| src/kraft/policy.py                      |      182 |        9 |     95% |114, 137, 148, 151, 193, 203, 207, 223, 227 |
| src/kraft/progress.py                    |       72 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py          |       56 |        9 |     84% |124-126, 134-139 |
| src/kraft/reattach.py                    |      171 |       21 |     88% |60-61, 69-70, 103-104, 194, 242-252, 332-333, 391, 397-398 |
| src/kraft/render.py                      |      188 |       15 |     92% |46-47, 76, 83, 338-345, 373, 385, 411 |
| src/kraft/review.py                      |       43 |        1 |     98% |        68 |
| src/kraft/sandbox.py                     |       96 |        9 |     91% |236, 257-258, 283-284, 299-300, 411-412 |
| src/kraft/skill.py                       |       40 |        3 |     92% | 72, 96-97 |
| src/kraft/steering.py                    |       40 |        0 |    100% |           |
| src/kraft/store/\_\_init\_\_.py          |        9 |        0 |    100% |           |
| src/kraft/store/\_common.py              |       15 |        0 |    100% |           |
| src/kraft/store/budget.py                |       51 |        0 |    100% |           |
| src/kraft/store/chain.py                 |       72 |        1 |     99% |       266 |
| src/kraft/store/counters.py              |       35 |        0 |    100% |           |
| src/kraft/store/gates.py                 |       22 |        0 |    100% |           |
| src/kraft/store/repos.py                 |       17 |        0 |    100% |           |
| src/kraft/store/sessions.py              |       89 |        0 |    100% |           |
| src/kraft/store/work\_items.py           |      121 |        0 |    100% |           |
| src/kraft/templates.py                   |      394 |       18 |     95% |210, 263, 393, 418, 424, 434, 443, 484, 696, 740, 743, 776, 788, 798, 808, 813, 904-908 |
| src/kraft/triggers.py                    |       38 |       10 |     74% |48-49, 66-73 |
| src/kraft/update.py                      |      102 |       10 |     90% |51-52, 64-68, 73, 89, 124-126 |
| src/kraft/usage.py                       |      151 |        5 |     97% |85, 202, 212, 257, 293 |
| src/kraft/worker\_env.py                 |        8 |        0 |    100% |           |
| src/kraft/worktree\_read.py              |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                          |       61 |        0 |    100% |           |
| **TOTAL**                                | **11200** |  **801** | **93%** |           |


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