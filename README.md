# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                     |    Stmts |     Miss |   Cover |   Missing |
|----------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py       |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py              |      113 |        3 |     97% |301-302, 304 |
| src/kraft/adapters/beads.py              |       62 |        8 |     87% |44, 83, 86-87, 125-126, 180-181 |
| src/kraft/adapters/forge/\_\_init\_\_.py |        9 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py           |       90 |        3 |     97% |74, 299-300 |
| src/kraft/adapters/forge/gh.py           |       79 |        7 |     91% |117, 136, 198, 206-209, 249 |
| src/kraft/adapters/forge/git.py          |       85 |       10 |     88% |162-169, 281-286, 296 |
| src/kraft/adapters/forge/glab.py         |      146 |        7 |     95% |224-225, 308, 340-341, 360, 382 |
| src/kraft/adapters/forge/models.py       |      100 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py           |      108 |        5 |     95% |67, 190, 242, 295-296 |
| src/kraft/adapters/forge/run.py          |      274 |       24 |     91% |304, 445, 685-686, 716-717, 952-979 |
| src/kraft/adapters/subprocess.py         |      229 |       12 |     95% |161-162, 176-177, 298, 345-346, 496-497, 526-527, 605 |
| src/kraft/analytics.py                   |      229 |        4 |     98% |50-51, 148, 154 |
| src/kraft/api/\_\_init\_\_.py            |       30 |        0 |    100% |           |
| src/kraft/api/deps.py                    |       97 |        4 |     96% |56-57, 175-176 |
| src/kraft/api/perimeter.py               |       70 |        0 |    100% |           |
| src/kraft/api/routes/\_\_init\_\_.py     |        0 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py        |       81 |        1 |     99% |       208 |
| src/kraft/api/routes/auth.py             |       45 |        6 |     87% |     54-61 |
| src/kraft/api/routes/board.py            |      105 |        1 |     99% |       126 |
| src/kraft/api/routes/gates.py            |      136 |       16 |     88% |29, 50-51, 56, 84, 102-103, 290, 292, 315, 331-332, 354-355, 379-380 |
| src/kraft/api/routes/lifecycle.py        |      475 |       38 |     92% |110, 113-114, 119-120, 199, 228, 243, 376, 447, 449, 451, 505-506, 539, 605-606, 643-644, 697, 720, 727, 758, 767, 865-866, 908-909, 964, 967, 1016, 1048-1049, 1127-1128, 1167, 1176-1177 |
| src/kraft/api/routes/repos.py            |      125 |        4 |     97% |57-61, 182-183 |
| src/kraft/api/routes/search.py           |      117 |       14 |     88% |47-48, 74-77, 80, 107-111, 134, 140-141, 171 |
| src/kraft/api/routes/sessions.py         |      106 |        5 |     95% |69, 192, 197, 199, 202 |
| src/kraft/api/routes/settings.py         |      369 |       25 |     93% |42, 132, 231, 233, 235, 353, 358-361, 376-377, 401-402, 404, 436-439, 442-443, 447-452, 509, 511 |
| src/kraft/api/routes/work\_items.py      |      200 |        7 |     96% |112, 221, 224, 256-259, 354-355 |
| src/kraft/api/startup.py                 |      117 |        4 |     97% |121-122, 247-248 |
| src/kraft/archive.py                     |       29 |        6 |     79% |     52-57 |
| src/kraft/auth.py                        |       74 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py       |       45 |        6 |     87% |   172-177 |
| src/kraft/builtins.py                    |      282 |       20 |     93% |60, 71-72, 80, 164, 241, 332-334, 375-376, 390-391, 533, 621-622, 822, 972-975, 1052 |
| src/kraft/ci\_wait.py                    |       53 |        9 |     83% |118-120, 128-133 |
| src/kraft/cli/\_\_init\_\_.py            |       39 |        1 |     97% |       118 |
| src/kraft/cli/admin.py                   |      341 |       43 |     87% |68-74, 151-163, 167-181, 254-255, 293-294, 324, 415, 495, 533-535, 565-566, 657 |
| src/kraft/cli/common.py                  |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                    |      108 |        5 |     95% |59, 87, 91, 97, 109 |
| src/kraft/cli/repo.py                    |       63 |        4 |     94% |69-70, 78-79 |
| src/kraft/cli/view.py                    |      173 |       11 |     94% |57-61, 103-105, 142-143, 167, 177, 227-228 |
| src/kraft/client/\_\_init\_\_.py         |        4 |        0 |    100% |           |
| src/kraft/client/actions.py              |      114 |       26 |     77% |38, 59, 62, 98, 135, 160-164, 175-176, 270-271, 292-308, 331-348 |
| src/kraft/client/context.py              |       47 |        2 |     96% |     32-33 |
| src/kraft/client/reads.py                |      128 |       14 |     89% |135, 188-189, 199-206, 230-233, 256, 262, 283 |
| src/kraft/client/transport.py            |       70 |        8 |     89% |52, 67-68, 114-115, 117, 127-128 |
| src/kraft/config.py                      |      253 |       14 |     94% |52, 70-72, 120, 151, 154, 202, 213, 269, 461-462, 481-482 |
| src/kraft/db.py                          |      108 |        0 |    100% |           |
| src/kraft/doctor.py                      |      287 |       35 |     88% |100, 102, 126-127, 144-145, 155-157, 187-188, 220, 224-225, 269-270, 299, 303-304, 364-365, 380-383, 459-460, 480-481, 504, 519, 527, 552, 556-557 |
| src/kraft/escalate.py                    |       83 |        5 |     94% |114, 151, 176-177, 288 |
| src/kraft/events.py                      |       17 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py       |        8 |        0 |    100% |           |
| src/kraft/executor/context.py            |       32 |        1 |     97% |       101 |
| src/kraft/executor/dispatch.py           |      289 |        6 |     98% |204, 524, 588, 601, 1103, 1109 |
| src/kraft/executor/entry.py              |       89 |        1 |     99% |       207 |
| src/kraft/executor/gates.py              |      241 |       17 |     93% |63-64, 95, 157, 365, 373, 387, 428, 442, 532, 585-586, 810-813, 815 |
| src/kraft/executor/prompts.py            |      167 |        2 |     99% |  179, 496 |
| src/kraft/executor/resuming.py           |       78 |       13 |     83% |57, 140, 171-174, 184-186, 222, 224, 226, 228 |
| src/kraft/executor/stops.py              |       48 |        5 |     90% |67, 98-104 |
| src/kraft/executor/walk.py               |      362 |       38 |     90% |260, 328, 330-335, 341, 367, 369, 398, 400, 409, 434, 474, 485, 487, 489, 491, 522, 537-544, 546, 550, 552, 778, 919, 921, 1002, 1066, 1071-1076, 1115, 1231, 1243 |
| src/kraft/findings.py                    |      108 |        0 |    100% |           |
| src/kraft/gate\_review.py                |       31 |        2 |     94% |    65, 88 |
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
| src/kraft/paths.py                       |       41 |        0 |    100% |           |
| src/kraft/policy.py                      |      182 |        9 |     95% |114, 137, 148, 151, 193, 203, 207, 223, 227 |
| src/kraft/progress.py                    |       63 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py          |       56 |        9 |     84% |124-126, 134-139 |
| src/kraft/reattach.py                    |      171 |       21 |     88% |60-61, 69-70, 103-104, 188, 236-246, 321-322, 380, 386-387 |
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
| src/kraft/templates.py                   |      367 |       17 |     95% |184, 236, 358, 364, 374, 383, 418, 597, 641, 644, 677, 689, 699, 709, 714, 805-809 |
| src/kraft/triggers.py                    |       38 |       10 |     74% |48-49, 66-73 |
| src/kraft/update.py                      |       93 |       10 |     89% |50-51, 63-67, 72, 88, 123-125 |
| src/kraft/usage.py                       |      121 |        4 |     97% |84, 201, 211, 256 |
| src/kraft/worker\_env.py                 |        8 |        0 |    100% |           |
| src/kraft/worktree\_read.py              |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                          |       61 |        0 |    100% |           |
| **TOTAL**                                | **10814** |  **767** | **93%** |           |


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