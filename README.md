# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                    |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py                  |      262 |       11 |     96% |332, 386-387, 389, 602, 706-709, 923-924 |
| src/kraft/adapters/artifact\_notes.py        |        2 |        0 |    100% |           |
| src/kraft/adapters/beads.py                  |       62 |       19 |     69% |82-88, 122, 125-126, 182-190 |
| src/kraft/adapters/forge/\_\_init\_\_.py     |        8 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py               |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/gh.py               |      156 |       12 |     92% |172, 252, 276, 282-292, 370, 421 |
| src/kraft/adapters/forge/git.py              |      138 |        5 |     96% |47, 323-330, 508 |
| src/kraft/adapters/forge/glab.py             |      180 |       10 |     94% |239-240, 327, 336, 359-360, 426, 429, 442, 464 |
| src/kraft/adapters/forge/models.py           |      170 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py               |      124 |        5 |     96% |74, 197, 249, 315-316 |
| src/kraft/adapters/forge/run.py              |      449 |       11 |     98% |425, 609-611, 854-855, 885-896, 1398, 1492 |
| src/kraft/adapters/hook\_install.py          |      232 |       10 |     96% |91, 185-188, 264-265, 290-291, 380 |
| src/kraft/adapters/profiles.py               |       33 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      408 |       12 |     97% |243-244, 258-259, 286, 487-488, 552, 886, 912-913, 1082 |
| src/kraft/analytics.py                       |      256 |        7 |     97% |52-53, 182, 188, 440-442 |
| src/kraft/api/\_\_init\_\_.py                |       34 |        0 |    100% |           |
| src/kraft/api/apidocs.py                     |       24 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      329 |       23 |     93% |111, 188, 203-204, 246, 282-283, 293-294, 393, 412, 468-471, 496, 547-548, 600-601, 616-617, 619, 625, 627 |
| src/kraft/api/deps.py                        |      319 |       21 |     93% |83-84, 211-212, 412, 415-417, 489-490, 528-529, 538-539, 672-673, 708, 711, 714, 737-738 |
| src/kraft/api/perimeter.py                   |      119 |        2 |     98% |  213, 368 |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       37 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py            |      181 |        7 |     96% |146, 245-252, 374 |
| src/kraft/api/routes/auth.py                 |       57 |        0 |    100% |           |
| src/kraft/api/routes/board.py                |      382 |        4 |     99% |168, 655, 751, 754 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      269 |        9 |     97% |239, 255, 310-311, 317, 333, 350, 389, 454 |
| src/kraft/api/routes/gates.py                |      136 |        6 |     96% |118, 121, 129, 333, 366-367 |
| src/kraft/api/routes/harnesses.py            |       97 |        1 |     99% |       253 |
| src/kraft/api/routes/lifecycle.py            |      806 |       46 |     94% |153, 156-157, 162-163, 267, 280, 362-365, 386-387, 408, 494, 514, 567, 691, 787-788, 798, 946-947, 1011, 1090, 1099, 1158, 1261-1262, 1469, 1474-1475, 1496, 1618, 1648-1649, 1712, 1732-1733, 1778-1779, 1781, 1822, 1941-1942, 1992 |
| src/kraft/api/routes/repos.py                |      233 |        8 |     97% |96-97, 175, 194, 266-267, 398-399 |
| src/kraft/api/routes/review.py               |      306 |       14 |     95% |65, 69, 76, 127, 152, 191, 193, 203, 264, 377-378, 423-424, 526 |
| src/kraft/api/routes/search.py               |      117 |       14 |     88% |47-48, 75-78, 81, 108-112, 135, 141-142, 172 |
| src/kraft/api/routes/sessions.py             |      181 |       11 |     94% |98, 145, 149, 293, 301, 376-378, 382, 384, 387 |
| src/kraft/api/routes/settings.py             |      442 |       30 |     93% |86-87, 129-130, 197-198, 213-214, 223-224, 238-239, 314, 356-357, 478-481, 484, 525-526, 532, 551-554, 557-558, 626-627, 642-643, 675 |
| src/kraft/api/routes/work\_items.py          |      389 |       31 |     92% |134, 406-409, 502-520, 675-676, 839, 856, 873-874, 970-971, 1028-1029, 1034 |
| src/kraft/api/startup.py                     |      173 |        6 |     97% |101-103, 112-113, 182-183 |
| src/kraft/apply.py                           |      105 |        8 |     92% |48, 68-69, 136-137, 151-152, 169 |
| src/kraft/archive.py                         |       33 |        6 |     82% |     58-63 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       48 |        6 |     88% |   181-186 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      471 |       24 |     95% |107-108, 116, 207, 273, 357, 371, 405, 409, 413, 419, 490-491, 511-512, 599, 784-785, 917, 933, 1035-1036, 1553-1556 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      281 |       18 |     94% |128, 318, 344, 355, 367-368, 381, 478, 551, 596-598, 638-643 |
| src/kraft/cli/\_\_init\_\_.py                |       56 |       12 |     79% |95-101, 134-141, 148 |
| src/kraft/cli/admin.py                       |      533 |       33 |     94% |73, 156-158, 176, 182-186, 195, 289, 316, 410-411, 449-450, 572, 678, 731-732, 850-851, 856, 859, 868-874, 1070, 1096 |
| src/kraft/cli/common.py                      |       25 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      257 |        9 |     96% |30, 150, 158, 218, 222, 238, 275-276, 319 |
| src/kraft/cli/repo.py                        |      286 |       11 |     96% |139, 147, 162, 175-176, 231, 233, 327, 358, 440-441 |
| src/kraft/cli/templates.py                   |       71 |        1 |     99% |        98 |
| src/kraft/cli/verify.py                      |      148 |        5 |     97% |128, 206-209 |
| src/kraft/cli/view.py                        |      229 |        9 |     96% |84, 197-198, 220, 230, 280-281, 306-307 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      196 |       25 |     87% |62, 164, 181, 200, 268, 282-283, 301, 323, 325, 351, 358-362, 441-442, 464, 490, 502, 537, 552, 576, 627 |
| src/kraft/client/context.py                  |       49 |        2 |     96% |     44-45 |
| src/kraft/client/reads.py                    |      166 |       11 |     93% |167, 243-244, 254-261, 326, 349, 355, 401 |
| src/kraft/client/transport.py                |       94 |        9 |     90% |54, 69-70, 87, 174-175, 177, 188-189 |
| src/kraft/config.py                          |      593 |       15 |     97% |103, 122, 179-181, 310, 509, 512, 593, 786, 830-831, 1047-1048, 1135 |
| src/kraft/config\_schemas.py                 |       74 |        7 |     91% |   162-168 |
| src/kraft/db.py                              |      142 |        4 |     97% |1154, 1180-1182 |
| src/kraft/detect.py                          |     1016 |       59 |     94% |202, 357-359, 366-368, 455-462, 483, 578-579, 593, 601, 615, 635, 680, 686, 900, 913, 931, 949, 951, 1012-1013, 1083, 1095, 1108, 1137-1139, 1149, 1290-1291, 1402, 1492-1493, 1495, 1668-1686, 1773, 1840 |
| src/kraft/doctor.py                          |      542 |       36 |     93% |123, 191-192, 226-227, 237-239, 251, 255-256, 295, 358-359, 398-405, 453-454, 524-525, 663-664, 717, 729-730, 869-870, 953, 971, 1008, 1016, 1100-1101, 1129, 1133-1134 |
| src/kraft/drafts/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/drafts/areas.py                    |        6 |        0 |    100% |           |
| src/kraft/drafts/authored.py                 |      239 |       14 |     94% |83, 134, 158, 247, 293, 305, 310, 318, 331-332, 352, 358, 376, 381 |
| src/kraft/drafts/config.py                   |      110 |        9 |     92% |43, 87-88, 106-107, 158-159, 161-162 |
| src/kraft/drafts/harnesses.py                |      322 |       20 |     94% |140, 158, 165, 228, 239, 242, 244, 255, 257, 265, 270, 275, 279, 346, 357, 370-371, 441-443 |
| src/kraft/drafts/intake.py                   |       94 |        6 |     94% |36, 50, 109-110, 124-125 |
| src/kraft/drafts/item.py                     |       88 |        2 |     98% |   88, 116 |
| src/kraft/drafts/ops.py                      |      632 |       66 |     90% |81, 95, 134-135, 147, 154, 159, 165, 171, 177-178, 184-185, 201, 206, 289, 294-295, 350, 400, 402, 431-432, 462, 493, 513, 530, 535-536, 541, 551, 564, 576-581, 593, 618, 648, 656, 662, 682, 695, 699, 702, 729, 734, 780, 805, 823, 840, 857, 859, 863-865, 868, 871, 886, 889, 928-929, 934-935 |
| src/kraft/drafts/policy.py                   |      175 |       13 |     93% |51, 65, 82-84, 92, 99, 167, 180-181, 241, 243, 246 |
| src/kraft/drafts/policy\_caps.py             |       60 |        3 |     95% | 43, 49-50 |
| src/kraft/drafts/repos.py                    |      134 |        6 |     96% |37, 45, 50, 74, 122-123 |
| src/kraft/drafts/resolve.py                  |      420 |       24 |     94% |121, 128-129, 185-186, 208, 271-272, 287-288, 308, 318, 327, 336, 356, 425, 446, 593, 601-602, 617, 649, 681-682 |
| src/kraft/drafts/store.py                    |       78 |        0 |    100% |           |
| src/kraft/escalate.py                        |      180 |        6 |     97% |188, 291, 410, 590-601, 651 |
| src/kraft/events.py                          |       28 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       54 |        2 |     96% |  215, 246 |
| src/kraft/executor/dispatch.py               |      695 |       21 |     97% |217, 254, 321, 343-344, 430, 454, 560, 571, 574, 605-606, 757-758, 979-980, 1302, 1310, 1960, 2312, 2318 |
| src/kraft/executor/entry.py                  |      122 |        1 |     99% |       313 |
| src/kraft/executor/fallback.py               |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      327 |       11 |     97% |95, 223, 338, 341, 344, 347, 724, 806, 859-860, 1158 |
| src/kraft/executor/prompts.py                |      197 |        6 |     97% |144-150, 237, 670 |
| src/kraft/executor/read\_only.py             |       75 |        2 |     97% |    70, 83 |
| src/kraft/executor/resuming.py               |       82 |        1 |     99% |       168 |
| src/kraft/executor/retry.py                  |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py                  |      144 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      575 |       13 |     98% |550, 667, 968, 992, 1000, 1073-1088, 1133, 1361, 1533, 1668, 1725, 1771 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       74 |        4 |     95% |103-106, 111, 187 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      335 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 404, 563, 565, 567, 576, 595-596, 633-635 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |151-152, 161-163 |
| src/kraft/index/embed.py                     |       58 |       12 |     79% |37-38, 63, 68-74, 94-96 |
| src/kraft/index/ingest.py                    |      225 |       11 |     95% |79-80, 152-153, 267, 357-359, 469-471 |
| src/kraft/index/service.py                   |      350 |       26 |     93% |140-142, 236-238, 274, 293-295, 349, 354, 357-358, 457, 479, 498-499, 515, 533, 537-538, 704, 707, 710, 713 |
| src/kraft/init.py                            |       51 |        2 |     96% |     62-64 |
| src/kraft/intake.py                          |      127 |       12 |     91% |89-91, 107, 150-151, 185-187, 206-211, 247 |
| src/kraft/intent.py                          |      130 |        5 |     96% |91-92, 186, 203, 234 |
| src/kraft/logs.py                            |      177 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 168-169, 177-178, 278 |
| src/kraft/mcp.py                             |      125 |        2 |     98% |   502-503 |
| src/kraft/mr\_poller.py                      |       66 |       11 |     83% |70-71, 83-84, 90, 123-128 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                       |       98 |        3 |     97% |116, 189-190 |
| src/kraft/paths.py                           |       59 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       70 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/policy.py                          |      694 |       20 |     97% |252-253, 1060, 1247, 1481-1490, 1570, 1577, 1591, 1595-1599, 1604 |
| src/kraft/progress.py                        |      113 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       58 |        9 |     84% |159-161, 169-174 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/render.py                          |      226 |       23 |     90% |46-47, 76, 83, 322, 344, 357, 363-367, 405-412, 440, 452, 478 |
| src/kraft/review.py                          |      113 |        4 |     96% |89, 219, 223, 271 |
| src/kraft/review\_reply.py                   |       80 |        7 |     91% |76, 94-95, 123-124, 129, 149 |
| src/kraft/skill.py                           |       50 |        5 |     90% |77-78, 103, 127-128 |
| src/kraft/store/\_\_init\_\_.py              |       13 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       24 |        1 |     96% |        77 |
| src/kraft/store/budget.py                    |       65 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      193 |        4 |     98% |321, 409-411 |
| src/kraft/store/counters.py                  |       45 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       47 |        0 |    100% |           |
| src/kraft/store/intake.py                    |       14 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      194 |        5 |     97% |217, 222, 229-230, 447 |
| src/kraft/store/sessions.py                  |      155 |        0 |    100% |           |
| src/kraft/store/work\_items.py               |      177 |        0 |    100% |           |
| src/kraft/templates/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py             |       40 |        0 |    100% |           |
| src/kraft/templates/environment.py           |      284 |        2 |     99% |  519, 521 |
| src/kraft/templates/forks.py                 |      100 |        0 |    100% |           |
| src/kraft/templates/library.py               |      402 |        4 |     99% |200, 202, 214, 221 |
| src/kraft/templates/models.py                |      702 |        7 |     99% |104, 107, 561, 739, 1320-1321, 1584 |
| src/kraft/templates/positions.py             |       57 |        0 |    100% |           |
| src/kraft/templates/retry.py                 |      105 |       11 |     90% |189, 191-197, 199-200, 207 |
| src/kraft/templates/revision.py              |      248 |        8 |     97% |216, 251, 300, 450, 491-492, 494, 508 |
| src/kraft/triggers.py                        |       52 |        6 |     88% |61-62, 101-104 |
| src/kraft/update.py                          |      141 |        9 |     94% |76-77, 107-111, 116, 177-179 |
| src/kraft/usage.py                           |      469 |       24 |     95% |115-116, 222, 353, 474, 534, 554-555, 558-559, 603, 682, 702-703, 877, 974-975, 1072, 1080-1081, 1083, 1094, 1114-1115 |
| src/kraft/waits.py                           |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      777 |       48 |     94% |314, 326-329, 335-336, 408, 522, 570, 579-580, 598, 648-649, 676, 679-682, 751, 949-950, 1060-1061, 1088, 1234-1235, 1283, 1374-1377, 1470-1471, 1493, 1496-1497, 1540, 1600-1601, 1642, 1720-1721, 1757-1758, 1839-1840, 1866-1867 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       301 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      159 |        7 |     96% |141, 146, 177-178, 279, 305-306 |
| src/kraft/worker/egress.py                   |      455 |       36 |     92% |289-290, 296, 337-339, 358, 394, 400, 416-417, 427, 445, 447, 507, 512, 557, 565-566, 670, 681, 696, 720-721, 723-724, 736, 739, 769-770, 820-825 |
| src/kraft/worker/env.py                      |       11 |        0 |    100% |           |
| src/kraft/worker/inject.py                   |      238 |       18 |     92% |194, 214, 230, 241, 253, 351, 365, 374, 384, 390, 393, 407, 410, 415, 424-427 |
| src/kraft/worker/kit.py                      |      421 |       10 |     98% |124, 238-240, 430, 669-670, 672, 698, 732 |
| src/kraft/worker/reattach.py                 |      265 |       20 |     92% |70-71, 79-80, 113-114, 213, 259, 269-270, 301, 475, 483-484, 542-543, 556-557, 570-571 |
| src/kraft/worker/refstore.py                 |      224 |        7 |     97% |187, 190, 306, 394-395, 423, 446 |
| src/kraft/worker/sandbox.py                  |      158 |        7 |     96% |109, 147-148, 153-154, 191, 216 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |44, 49-50, 52 |
| src/kraft/worker/shim.py                     |        4 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       94 |        8 |     91% |152-153, 164-166, 192-193, 203 |
| src/kraft/worker/worktree\_read.py           |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                              |       72 |        3 |     96% |     77-79 |
| **TOTAL**                                    | **28577** | **1363** | **95%** |           |


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