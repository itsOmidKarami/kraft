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
| src/kraft/adapters/forge/git.py              |      199 |       14 |     93% |52, 207-208, 219-220, 252-254, 291-293, 484-491, 669 |
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
| src/kraft/api/perimeter.py                   |      133 |        2 |     98% |  271, 426 |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       37 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py            |      197 |        7 |     96% |147, 246-253, 410 |
| src/kraft/api/routes/auth.py                 |       60 |        0 |    100% |           |
| src/kraft/api/routes/board.py                |      382 |        4 |     99% |168, 655, 751, 754 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      270 |        9 |     97% |239, 255, 313-314, 320, 336, 353, 392, 457 |
| src/kraft/api/routes/gates.py                |      136 |        6 |     96% |118, 121, 129, 333, 366-367 |
| src/kraft/api/routes/harnesses.py            |       97 |        1 |     99% |       253 |
| src/kraft/api/routes/lifecycle.py            |      810 |       45 |     94% |156-157, 162-163, 267, 280, 362-365, 386-387, 408, 494, 514, 567, 693, 711, 797-798, 808, 956-957, 1100, 1109, 1168, 1271-1272, 1479, 1484-1485, 1506, 1628, 1658-1659, 1722, 1742-1743, 1788-1789, 1791, 1832, 1951-1952, 2002 |
| src/kraft/api/routes/repos.py                |      234 |        8 |     97% |96-97, 175, 194, 272-273, 404-405 |
| src/kraft/api/routes/review.py               |      328 |       15 |     95% |78, 82, 93, 156, 181, 213, 252, 254, 264, 325, 438-439, 484-485, 587 |
| src/kraft/api/routes/search.py               |      121 |       14 |     88% |48-49, 83-86, 89, 116-120, 143, 149-150, 180 |
| src/kraft/api/routes/sessions.py             |      181 |       11 |     94% |98, 145, 149, 293, 301, 376-378, 382, 384, 387 |
| src/kraft/api/routes/settings.py             |      442 |       30 |     93% |86-87, 129-130, 197-198, 213-214, 223-224, 238-239, 314, 356-357, 478-481, 484, 525-526, 532, 551-554, 557-558, 626-627, 642-643, 675 |
| src/kraft/api/routes/work\_items.py          |      404 |       31 |     92% |135, 412-415, 508-526, 694-695, 880, 897, 914-915, 1013-1014, 1071-1072, 1077 |
| src/kraft/api/startup.py                     |      178 |        6 |     97% |103-105, 116-117, 186-187 |
| src/kraft/apply.py                           |      105 |        8 |     92% |48, 68-69, 136-137, 151-152, 169 |
| src/kraft/archive.py                         |       33 |        6 |     82% |     58-63 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       48 |        6 |     88% |   181-186 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      540 |       28 |     95% |108-109, 117, 208, 274, 358, 372, 406, 410, 414, 420, 491-492, 512-513, 600, 785-786, 918, 934, 990-991, 1090-1091, 1321, 1345, 1709-1712 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      281 |       18 |     94% |128, 318, 344, 355, 367-368, 381, 478, 551, 596-598, 638-643 |
| src/kraft/cli/\_\_init\_\_.py                |       56 |       12 |     79% |95-101, 134-141, 148 |
| src/kraft/cli/admin.py                       |      575 |       38 |     93% |73, 156-158, 176, 182-186, 195, 229, 350, 377, 507-508, 629, 652-655, 749, 802-803, 921-922, 927, 930, 939-945, 984-985, 1154, 1180 |
| src/kraft/cli/common.py                      |       39 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      303 |       10 |     97% |30, 167, 175, 287, 291, 307, 344-345, 357, 404 |
| src/kraft/cli/repo.py                        |      294 |       11 |     96% |139, 147, 162, 175-176, 231, 233, 333, 364, 453-454 |
| src/kraft/cli/templates.py                   |       71 |        1 |     99% |        98 |
| src/kraft/cli/verify.py                      |      154 |        5 |     97% |133, 211-214 |
| src/kraft/cli/view.py                        |      265 |        9 |     97% |85, 225-226, 248, 258, 308-309, 362-363 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      196 |       25 |     87% |62, 164, 181, 200, 268, 282-283, 301, 323, 325, 351, 358-362, 441-442, 464, 490, 502, 537, 552, 576, 638 |
| src/kraft/client/context.py                  |       49 |        2 |     96% |     44-45 |
| src/kraft/client/reads.py                    |      171 |       11 |     94% |173, 249-250, 260-267, 332, 355, 361, 407 |
| src/kraft/client/transport.py                |       95 |        9 |     91% |54, 69-70, 87, 177-178, 180, 191-192 |
| src/kraft/config.py                          |      602 |       15 |     98% |103, 122, 179-181, 310, 509, 512, 593, 786, 830-831, 1047-1048, 1135 |
| src/kraft/config\_schemas.py                 |       74 |        7 |     91% |   162-168 |
| src/kraft/db.py                              |      142 |        4 |     97% |1154, 1180-1182 |
| src/kraft/detect.py                          |     1050 |       61 |     94% |202, 357-359, 366-368, 455-462, 483, 578-579, 593, 601, 615, 635, 680, 686, 900, 913, 931, 949, 951, 1012-1013, 1099, 1102, 1178, 1190, 1203, 1232-1234, 1244, 1385-1386, 1497, 1587-1588, 1590, 1763-1781, 1868, 1935 |
| src/kraft/doctor.py                          |      568 |       35 |     94% |125, 227-228, 262, 266, 284, 288-289, 331, 394-395, 434-441, 489-490, 560-561, 699-700, 753, 765-766, 905-906, 989, 1007, 1020, 1030, 1072, 1080, 1173-1174, 1202, 1206-1207 |
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
| src/kraft/drafts/repos.py                    |      137 |        6 |     96% |37, 45, 50, 74, 122-123 |
| src/kraft/drafts/resolve.py                  |      425 |       24 |     94% |121, 128-129, 185-186, 208, 302-303, 318-319, 339, 349, 358, 367, 387, 456, 477, 624, 632-633, 648, 680, 712-713 |
| src/kraft/drafts/store.py                    |       78 |        0 |    100% |           |
| src/kraft/escalate.py                        |      180 |        6 |     97% |188, 291, 410, 590-601, 651 |
| src/kraft/events.py                          |       28 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       54 |        2 |     96% |  215, 246 |
| src/kraft/executor/dispatch.py               |      701 |       21 |     97% |217, 254, 321, 343-344, 430, 454, 560, 571, 574, 605-606, 757-758, 979-980, 1329, 1337, 1987, 2339, 2345 |
| src/kraft/executor/entry.py                  |      122 |        1 |     99% |       313 |
| src/kraft/executor/fallback.py               |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      327 |       11 |     97% |95, 223, 338, 341, 344, 347, 724, 806, 859-860, 1158 |
| src/kraft/executor/prompts.py                |      197 |        6 |     97% |144-150, 237, 670 |
| src/kraft/executor/read\_only.py             |       75 |        2 |     97% |    70, 83 |
| src/kraft/executor/resuming.py               |       82 |        1 |     99% |       168 |
| src/kraft/executor/retry.py                  |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py                  |      146 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      575 |       13 |     98% |550, 667, 968, 992, 1000, 1073-1088, 1133, 1361, 1533, 1668, 1725, 1771 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       74 |        4 |     95% |103-106, 111, 187 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      335 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 404, 563, 565, 567, 576, 595-596, 633-635 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |151-152, 161-163 |
| src/kraft/index/embed.py                     |       61 |       13 |     79% |37-38, 63, 68, 73-79, 99-101 |
| src/kraft/index/ingest.py                    |      225 |       11 |     95% |79-80, 152-153, 267, 357-359, 469-471 |
| src/kraft/index/service.py                   |      369 |       26 |     93% |140-142, 236-238, 274, 293-295, 348-349, 354, 357-358, 498-499, 515, 533, 537-538, 714, 717, 720, 723, 785 |
| src/kraft/init.py                            |       51 |        2 |     96% |     62-64 |
| src/kraft/intake.py                          |      127 |       12 |     91% |89-91, 107, 150-151, 185-187, 206-211, 247 |
| src/kraft/intent.py                          |      198 |        9 |     95% |91-92, 186, 203, 264-265, 321-322, 339 |
| src/kraft/logs.py                            |      177 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 168-169, 177-178, 278 |
| src/kraft/mcp.py                             |      128 |        2 |     98% |   525-526 |
| src/kraft/mr\_poller.py                      |       66 |       11 |     83% |70-71, 83-84, 90, 123-128 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                       |       98 |        3 |     97% |116, 189-190 |
| src/kraft/paths.py                           |       59 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       70 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/pidfile.py                         |      125 |       19 |     85% |51-52, 57-58, 80-81, 117, 134, 145-146, 160-161, 169-170, 172, 196-197, 199-200 |
| src/kraft/policy.py                          |      694 |       20 |     97% |252-253, 1060, 1247, 1481-1490, 1570, 1577, 1591, 1595-1599, 1604 |
| src/kraft/progress.py                        |      113 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       58 |        9 |     84% |159-161, 169-174 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/render.py                          |      235 |       24 |     90% |46-47, 76, 83, 332, 343, 358, 371, 377-381, 419-426, 465, 477, 503 |
| src/kraft/review.py                          |      170 |        5 |     97% |89, 219, 223, 271, 324 |
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
| src/kraft/store/review.py                    |      183 |        4 |     98% |217, 222, 229-230 |
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
| src/kraft/update.py                          |      201 |       12 |     94% |72-73, 103-107, 112, 173-175, 285, 293-294 |
| src/kraft/usage.py                           |      484 |       26 |     95% |117-118, 224, 355, 476, 536, 556-557, 560-561, 605, 684, 704-705, 879, 976-977, 1074, 1082-1083, 1085, 1096, 1116-1117, 1266, 1278 |
| src/kraft/waits.py                           |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      796 |       55 |     93% |324, 336-339, 351-358, 435, 550, 598, 607-608, 626, 656, 681-682, 709, 712-715, 784, 982-983, 1093-1094, 1121, 1267-1268, 1316, 1407-1410, 1503-1504, 1526, 1529-1530, 1573, 1633-1634, 1675, 1753-1754, 1790-1791, 1872-1873, 1899-1900 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       301 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      159 |        7 |     96% |141, 146, 177-178, 279, 305-306 |
| src/kraft/worker/egress.py                   |      455 |       36 |     92% |289-290, 296, 337-339, 358, 394, 400, 416-417, 427, 445, 447, 507, 512, 557, 565-566, 670, 681, 696, 720-721, 723-724, 736, 739, 769-770, 820-825 |
| src/kraft/worker/env.py                      |       11 |        0 |    100% |           |
| src/kraft/worker/inject.py                   |      238 |       18 |     92% |194, 214, 230, 241, 253, 351, 365, 374, 384, 390, 393, 407, 410, 415, 424-427 |
| src/kraft/worker/kit.py                      |      421 |       10 |     98% |124, 238-240, 430, 669-670, 672, 698, 732 |
| src/kraft/worker/reattach.py                 |      272 |       20 |     93% |89-90, 98-99, 132-133, 232, 278, 288-289, 320, 494, 502-503, 561-562, 575-576, 589-590 |
| src/kraft/worker/refstore.py                 |      224 |        7 |     97% |187, 190, 306, 394-395, 423, 446 |
| src/kraft/worker/sandbox.py                  |      158 |        7 |     96% |109, 147-148, 153-154, 191, 216 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |44, 49-50, 52 |
| src/kraft/worker/shim.py                     |        4 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       94 |        8 |     91% |152-153, 164-166, 192-193, 203 |
| src/kraft/worker/worktree\_read.py           |       48 |        8 |     83% |82, 86-88, 91-95, 103-104 |
| src/kraft/ws.py                              |       72 |        3 |     96% |     77-79 |
| **TOTAL**                                    | **29409** | **1420** | **95%** |           |


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