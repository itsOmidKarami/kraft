# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                    |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py                  |      262 |       11 |     96% |332, 386-387, 389, 602, 706-709, 923-924 |
| src/kraft/adapters/artifact\_notes.py        |        2 |        0 |    100% |           |
| src/kraft/adapters/beads.py                  |       62 |       28 |     55% |40-45, 49, 82-88, 122, 125-126, 178-190 |
| src/kraft/adapters/forge/\_\_init\_\_.py     |        8 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py               |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/gh.py               |      156 |       14 |     91% |172, 252, 276, 282-292, 314-315, 370, 421 |
| src/kraft/adapters/forge/git.py              |      132 |        5 |     96% |47, 282-289, 467 |
| src/kraft/adapters/forge/glab.py             |      180 |        9 |     95% |239-240, 327, 359-360, 426, 429, 442, 464 |
| src/kraft/adapters/forge/models.py           |      170 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py               |      124 |        5 |     96% |74, 197, 249, 315-316 |
| src/kraft/adapters/forge/run.py              |      449 |       11 |     98% |425, 609-611, 854-855, 885-896, 1398, 1492 |
| src/kraft/adapters/hook\_install.py          |      149 |        5 |     97% |110-113, 220 |
| src/kraft/adapters/profiles.py               |       33 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      408 |       12 |     97% |243-244, 258-259, 286, 487-488, 552, 886, 912-913, 1082 |
| src/kraft/analytics.py                       |      256 |        7 |     97% |52-53, 182, 188, 440-442 |
| src/kraft/api/\_\_init\_\_.py                |       30 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      329 |       24 |     93% |111, 188, 203-204, 246, 282-283, 293-294, 367, 387, 406, 462-465, 490, 541-542, 594-595, 610-611, 613, 619, 621 |
| src/kraft/api/deps.py                        |      313 |       21 |     93% |83-84, 211-212, 396, 399-401, 473-474, 512-513, 522-523, 656-657, 692, 695, 698, 721-722 |
| src/kraft/api/perimeter.py                   |       77 |        0 |    100% |           |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       37 |        2 |     95% |     34-35 |
| src/kraft/api/routes/artifacts.py            |      181 |       11 |     94% |144, 157, 162-163, 214, 242-249, 371 |
| src/kraft/api/routes/auth.py                 |       57 |        6 |     89% |     81-88 |
| src/kraft/api/routes/board.py                |      310 |        6 |     98% |141, 608, 669, 673, 684-685 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      269 |        9 |     97% |239, 255, 310-311, 317, 333, 350, 389, 454 |
| src/kraft/api/routes/gates.py                |      136 |        9 |     93% |118, 121, 129, 310, 332, 365-366, 391-392 |
| src/kraft/api/routes/harnesses.py            |       97 |        1 |     99% |       252 |
| src/kraft/api/routes/lifecycle.py            |      715 |       40 |     94% |152, 155-156, 161-162, 247-248, 270, 301, 333, 351, 402, 525, 621-622, 632, 780-781, 843, 886, 891, 922, 931, 990, 1093-1094, 1359, 1389-1390, 1453, 1473-1474, 1517-1518, 1520, 1560, 1679-1680, 1719, 1729 |
| src/kraft/api/routes/repos.py                |      180 |        6 |     97% |85-89, 126, 145, 282-283 |
| src/kraft/api/routes/review.py               |      292 |       40 |     86% |46, 51, 83, 108, 142-145, 148, 150, 160, 171-172, 185-190, 195-211, 216-219, 227, 340-341, 386-387, 456, 488 |
| src/kraft/api/routes/search.py               |      117 |       14 |     88% |47-48, 74-77, 80, 107-111, 134, 140-141, 171 |
| src/kraft/api/routes/sessions.py             |      180 |       11 |     94% |96, 142, 146, 285, 293, 372-374, 378, 380, 383 |
| src/kraft/api/routes/settings.py             |      408 |       32 |     92% |83-84, 123-124, 191-192, 207-208, 217-218, 232-233, 308, 350-351, 470-473, 476, 516-517, 523, 542-545, 548-549, 553-558, 616 |
| src/kraft/api/routes/work\_items.py          |      372 |       37 |     90% |135, 378-381, 467-485, 526-527, 547-548, 571-572, 624-625, 788, 805, 822-823, 919-920, 972-973, 978 |
| src/kraft/api/startup.py                     |      158 |        4 |     97% |99-101, 158-159 |
| src/kraft/apply.py                           |      105 |        8 |     92% |48, 68-69, 136-137, 151-152, 169 |
| src/kraft/archive.py                         |       29 |        6 |     79% |     52-57 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       48 |        8 |     83% |140-148, 181-186 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      469 |       23 |     95% |107-108, 116, 207, 273, 357, 371, 405, 409, 413, 419, 490-491, 511-512, 771-772, 903, 919, 1021-1022, 1539-1542 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      270 |       17 |     94% |292, 318, 329, 341-342, 355, 452, 525, 570-572, 610-615 |
| src/kraft/cli/\_\_init\_\_.py                |       39 |        1 |     97% |       118 |
| src/kraft/cli/admin.py                       |      483 |       35 |     93% |72, 155-157, 175, 181-185, 194, 279, 306, 347, 394-395, 433-434, 464, 555, 661, 708-709, 739-740, 745, 748, 757-763, 946, 965 |
| src/kraft/cli/common.py                      |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      245 |        9 |     96% |30, 116, 124, 184, 188, 204, 241-242, 285 |
| src/kraft/cli/repo.py                        |       71 |        4 |     94% |69-70, 92-93 |
| src/kraft/cli/templates.py                   |       71 |        1 |     99% |        98 |
| src/kraft/cli/view.py                        |      226 |       11 |     95% |84, 105-109, 191-192, 214, 224, 274-275, 300-301 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      174 |       33 |     81% |63, 137, 181, 215, 229-230, 248, 270, 272, 295, 302-306, 311-312, 329-330, 379-380, 400, 425-441, 470, 485, 507, 550, 555 |
| src/kraft/client/context.py                  |       49 |        2 |     96% |     44-45 |
| src/kraft/client/reads.py                    |      161 |       14 |     91% |151, 221-222, 232-239, 297-300, 323, 329, 375 |
| src/kraft/client/transport.py                |       70 |        8 |     89% |52, 67-68, 114-115, 117, 128-129 |
| src/kraft/config.py                          |      488 |       16 |     97% |101, 119-121, 250, 449, 452, 533, 711-712, 757-758, 779-780, 809, 958 |
| src/kraft/config\_schemas.py                 |       73 |        7 |     90% |   160-166 |
| src/kraft/db.py                              |      115 |        0 |    100% |           |
| src/kraft/doctor.py                          |      467 |       33 |     93% |123, 166-167, 185-186, 196-198, 210, 214-215, 254, 317-318, 355-362, 406-407, 477-478, 585, 597-598, 733-734, 817, 859, 867, 950-951, 976, 980-981 |
| src/kraft/drafts/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/drafts/areas.py                    |        6 |        0 |    100% |           |
| src/kraft/drafts/authored.py                 |      239 |       14 |     94% |83, 134, 158, 247, 293, 305, 310, 318, 331-332, 352, 358, 376, 381 |
| src/kraft/drafts/config.py                   |      110 |        9 |     92% |43, 87-88, 106-107, 158-159, 161-162 |
| src/kraft/drafts/harnesses.py                |      264 |       20 |     92% |134, 152, 159, 222, 233, 236, 238, 249, 251, 259, 264, 269, 273, 296, 307, 320-321, 379-381 |
| src/kraft/drafts/intake.py                   |       94 |        6 |     94% |36, 50, 109-110, 124-125 |
| src/kraft/drafts/item.py                     |       88 |        2 |     98% |   88, 116 |
| src/kraft/drafts/ops.py                      |      632 |       66 |     90% |81, 95, 134-135, 147, 154, 159, 165, 171, 177-178, 184-185, 201, 206, 289, 294-295, 350, 400, 402, 431-432, 462, 493, 513, 530, 538-539, 544, 554, 567, 579-584, 596, 621, 651, 659, 665, 685, 695, 699, 702, 729, 734, 780, 805, 823, 840, 857, 859, 863-865, 868, 871, 886, 889, 928-929, 934-935 |
| src/kraft/drafts/policy.py                   |      175 |       13 |     93% |51, 65, 82-84, 92, 99, 167, 180-181, 241, 243, 246 |
| src/kraft/drafts/policy\_caps.py             |       60 |        3 |     95% | 43, 49-50 |
| src/kraft/drafts/repos.py                    |      130 |        6 |     95% |36, 44, 49, 69, 117-118 |
| src/kraft/drafts/resolve.py                  |      407 |       26 |     94% |119, 126-127, 183-184, 206, 255-256, 271-272, 292, 302, 311, 320, 340, 389, 399, 411, 420, 567, 575-576, 591, 623, 655-656 |
| src/kraft/drafts/store.py                    |       78 |        0 |    100% |           |
| src/kraft/escalate.py                        |      180 |        6 |     97% |188, 291, 410, 590-601, 651 |
| src/kraft/events.py                          |       28 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       54 |        2 |     96% |  215, 246 |
| src/kraft/executor/dispatch.py               |      688 |       21 |     97% |216, 253, 320, 342-343, 429, 453, 549, 560, 563, 594-595, 746-747, 963-964, 1286, 1294, 1944, 2296, 2302 |
| src/kraft/executor/entry.py                  |      122 |        1 |     99% |       313 |
| src/kraft/executor/fallback.py               |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      326 |       12 |     96% |95, 223, 338, 341, 344, 347, 587, 720, 802, 855-856, 1154 |
| src/kraft/executor/prompts.py                |      197 |        6 |     97% |144-150, 237, 670 |
| src/kraft/executor/read\_only.py             |       75 |        2 |     97% |    70, 83 |
| src/kraft/executor/resuming.py               |       82 |        1 |     99% |       168 |
| src/kraft/executor/retry.py                  |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py                  |      139 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      566 |       13 |     98% |546, 663, 947, 971, 979, 1052-1067, 1112, 1340, 1507, 1642, 1699, 1745 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       68 |        4 |     94% |102-105, 110, 186 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      335 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 404, 563, 565, 567, 576, 595-596, 633-635 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |151-152, 161-163 |
| src/kraft/index/embed.py                     |       58 |       15 |     74% |34-38, 63, 68-74, 94-96 |
| src/kraft/index/ingest.py                    |      209 |       11 |     95% |77-78, 142-143, 233, 323-325, 435-437 |
| src/kraft/index/service.py                   |      363 |       34 |     91% |140-142, 193-195, 229-231, 267, 286-288, 341-342, 347, 350-351, 450, 472, 475-476, 495-496, 512, 530, 534-535, 701, 704, 707, 711-712, 714 |
| src/kraft/init.py                            |       51 |        4 |     92% |62-64, 125-126 |
| src/kraft/intake.py                          |      127 |       12 |     91% |89-91, 107, 150-151, 185-187, 206-211, 247 |
| src/kraft/intent.py                          |      130 |        9 |     93% |91-92, 172-186, 203, 234 |
| src/kraft/logs.py                            |      177 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 168-169, 177-178, 278 |
| src/kraft/mcp.py                             |      103 |       22 |     79% |31, 38, 43, 50, 149, 156, 163, 170, 182, 196, 206, 217, 226, 233, 242, 250, 258, 272, 287, 426, 435-436 |
| src/kraft/mr\_poller.py                      |       66 |       12 |     82% |70-71, 83-84, 90, 104, 123-128 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                       |       73 |        3 |     96% |55, 128-129 |
| src/kraft/paths.py                           |       59 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       69 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/policy.py                          |      693 |       20 |     97% |252-253, 1053, 1240, 1474-1483, 1563, 1570, 1584, 1588-1592, 1597 |
| src/kraft/progress.py                        |      113 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       58 |        9 |     84% |159-161, 169-174 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/render.py                          |      220 |       23 |     90% |46-47, 76, 83, 312, 329, 342, 348-352, 390-397, 425, 437, 463 |
| src/kraft/review.py                          |      113 |        6 |     95% |89, 219, 223, 257, 259, 271 |
| src/kraft/review\_reply.py                   |       75 |        7 |     91% |75, 93-94, 122-123, 128, 142 |
| src/kraft/skill.py                           |       50 |        5 |     90% |77-78, 103, 127-128 |
| src/kraft/store/\_\_init\_\_.py              |       13 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       24 |        1 |     96% |        77 |
| src/kraft/store/budget.py                    |       65 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      183 |        4 |     98% |321, 409-411 |
| src/kraft/store/counters.py                  |       45 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       47 |        0 |    100% |           |
| src/kraft/store/intake.py                    |       14 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      185 |        6 |     97% |197, 202, 209-210, 224, 231 |
| src/kraft/store/sessions.py                  |      155 |        0 |    100% |           |
| src/kraft/store/work\_items.py               |      164 |        0 |    100% |           |
| src/kraft/templates/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py             |       28 |        0 |    100% |           |
| src/kraft/templates/environment.py           |      284 |        2 |     99% |  519, 521 |
| src/kraft/templates/forks.py                 |      100 |        0 |    100% |           |
| src/kraft/templates/library.py               |      400 |        4 |     99% |199, 201, 213, 220 |
| src/kraft/templates/models.py                |      684 |        7 |     99% |104, 107, 490, 668, 1249-1250, 1513 |
| src/kraft/templates/positions.py             |       57 |        0 |    100% |           |
| src/kraft/templates/retry.py                 |      100 |       11 |     89% |183, 185-191, 193-194, 201 |
| src/kraft/templates/revision.py              |      248 |        8 |     97% |216, 251, 300, 450, 491-492, 494, 508 |
| src/kraft/triggers.py                        |       52 |        6 |     88% |61-62, 101-104 |
| src/kraft/update.py                          |      141 |        9 |     94% |76-77, 107-111, 116, 177-179 |
| src/kraft/usage.py                           |      469 |       26 |     94% |115-116, 222, 353, 474, 534, 554-555, 558-559, 603, 682, 702-703, 877, 952-953, 974-975, 1072, 1080-1081, 1083, 1094, 1114-1115 |
| src/kraft/waits.py                           |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      659 |       44 |     93% |283, 295-298, 304-305, 353, 422-423, 450, 453-456, 523, 721-722, 832-833, 860, 1006-1007, 1055, 1145-1148, 1241-1242, 1264, 1267-1268, 1311, 1371-1372, 1404-1405, 1483-1484, 1520-1521, 1598-1599, 1619-1620 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       301 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      159 |        7 |     96% |141, 146, 177-178, 279, 305-306 |
| src/kraft/worker/egress.py                   |      454 |       36 |     92% |289-290, 296, 337-339, 358, 394, 400, 416-417, 427, 445, 447, 507, 512, 557, 565-566, 666, 677, 692, 716-717, 719-720, 732, 735, 765-766, 816-821 |
| src/kraft/worker/env.py                      |       10 |        0 |    100% |           |
| src/kraft/worker/inject.py                   |      238 |       18 |     92% |194, 214, 230, 241, 253, 351, 365, 374, 384, 390, 393, 407, 410, 415, 424-427 |
| src/kraft/worker/kit.py                      |      421 |       10 |     98% |124, 238-240, 430, 669-670, 672, 698, 732 |
| src/kraft/worker/reattach.py                 |      265 |       20 |     92% |70-71, 79-80, 113-114, 213, 259, 269-270, 301, 473, 481-482, 540-541, 554-555, 568-569 |
| src/kraft/worker/refstore.py                 |      224 |        7 |     97% |187, 190, 306, 394-395, 423, 446 |
| src/kraft/worker/sandbox.py                  |      158 |        7 |     96% |109, 147-148, 153-154, 191, 216 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |44, 49-50, 52 |
| src/kraft/worker/shim.py                     |        4 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       94 |        8 |     91% |152-153, 164-166, 192-193, 203 |
| src/kraft/worker/worktree\_read.py           |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                              |       72 |        3 |     96% |     77-79 |
| **TOTAL**                                    | **26019** | **1376** | **95%** |           |


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