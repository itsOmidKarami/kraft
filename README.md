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
| src/kraft/adapters/hook\_install.py          |      207 |        9 |     96% |128-131, 207-208, 233-234, 323 |
| src/kraft/adapters/profiles.py               |       33 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      408 |       12 |     97% |243-244, 258-259, 286, 487-488, 552, 886, 912-913, 1082 |
| src/kraft/analytics.py                       |      256 |        7 |     97% |52-53, 182, 188, 440-442 |
| src/kraft/api/\_\_init\_\_.py                |       34 |        0 |    100% |           |
| src/kraft/api/apidocs.py                     |       24 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      329 |       23 |     93% |111, 188, 203-204, 246, 282-283, 293-294, 393, 412, 468-471, 496, 547-548, 600-601, 616-617, 619, 625, 627 |
| src/kraft/api/deps.py                        |      317 |       21 |     93% |83-84, 211-212, 405, 408-410, 482-483, 521-522, 531-532, 665-666, 701, 704, 707, 730-731 |
| src/kraft/api/perimeter.py                   |      119 |        2 |     98% |  213, 368 |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       37 |        2 |     95% |     34-35 |
| src/kraft/api/routes/artifacts.py            |      181 |       10 |     94% |146, 164-165, 217, 245-252, 374 |
| src/kraft/api/routes/auth.py                 |       57 |        6 |     89% |     81-88 |
| src/kraft/api/routes/board.py                |      381 |        8 |     98% |168, 655, 751, 754, 836, 840, 851-852 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      269 |        9 |     97% |239, 255, 310-311, 317, 333, 350, 389, 454 |
| src/kraft/api/routes/gates.py                |      136 |        9 |     93% |118, 121, 129, 311, 333, 366-367, 392-393 |
| src/kraft/api/routes/harnesses.py            |       97 |        1 |     99% |       253 |
| src/kraft/api/routes/lifecycle.py            |      806 |       48 |     94% |153, 156-157, 162-163, 267, 280, 362-365, 386-387, 408, 494, 514, 567, 691, 787-788, 798, 946-947, 1011, 1054, 1059, 1090, 1099, 1158, 1261-1262, 1469, 1474-1475, 1496, 1618, 1648-1649, 1712, 1732-1733, 1778-1779, 1781, 1822, 1941-1942, 1992 |
| src/kraft/api/routes/repos.py                |      183 |        6 |     97% |86-90, 127, 146, 286-287 |
| src/kraft/api/routes/review.py               |      292 |       40 |     86% |46, 51, 83, 108, 142-145, 148, 150, 160, 171-172, 185-190, 195-211, 216-219, 227, 340-341, 386-387, 457, 489 |
| src/kraft/api/routes/search.py               |      117 |       14 |     88% |47-48, 75-78, 81, 108-112, 135, 141-142, 172 |
| src/kraft/api/routes/sessions.py             |      177 |       11 |     94% |97, 144, 148, 289, 297, 372-374, 378, 380, 383 |
| src/kraft/api/routes/settings.py             |      419 |       32 |     92% |84-85, 127-128, 195-196, 211-212, 221-222, 236-237, 312, 354-355, 476-479, 482, 523-524, 530, 549-552, 555-556, 560-565, 639 |
| src/kraft/api/routes/work\_items.py          |      375 |       37 |     90% |134, 379-382, 468-486, 533-534, 554-555, 578-579, 641-642, 805, 822, 839-840, 936-937, 992-993, 998 |
| src/kraft/api/startup.py                     |      173 |        6 |     97% |101-103, 112-113, 182-183 |
| src/kraft/apply.py                           |      105 |        8 |     92% |48, 68-69, 136-137, 151-152, 169 |
| src/kraft/archive.py                         |       33 |        6 |     82% |     58-63 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       48 |        8 |     83% |140-148, 181-186 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      469 |       23 |     95% |107-108, 116, 207, 273, 357, 371, 405, 409, 413, 419, 490-491, 511-512, 771-772, 904, 920, 1022-1023, 1540-1543 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      281 |       18 |     94% |128, 318, 344, 355, 367-368, 381, 478, 551, 596-598, 638-643 |
| src/kraft/cli/\_\_init\_\_.py                |       56 |       12 |     79% |95-101, 134-141, 148 |
| src/kraft/cli/admin.py                       |      527 |       33 |     94% |73, 156-158, 176, 182-186, 195, 280, 307, 401-402, 440-441, 563, 669, 722-723, 841-842, 847, 850, 859-865, 1061, 1087 |
| src/kraft/cli/common.py                      |       21 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      251 |        9 |     96% |30, 137, 145, 205, 209, 225, 262-263, 306 |
| src/kraft/cli/repo.py                        |       73 |        4 |     95% |69-70, 102-103 |
| src/kraft/cli/templates.py                   |       71 |        1 |     99% |        98 |
| src/kraft/cli/view.py                        |      229 |       11 |     95% |84, 105-109, 197-198, 220, 230, 280-281, 306-307 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      173 |       24 |     86% |62, 136, 180, 214, 228-229, 247, 269, 271, 297, 304-308, 387-388, 410, 438, 442, 477, 492, 516, 567 |
| src/kraft/client/context.py                  |       49 |        2 |     96% |     44-45 |
| src/kraft/client/reads.py                    |      167 |       14 |     92% |167, 243-244, 254-261, 319-322, 345, 351, 397 |
| src/kraft/client/transport.py                |       78 |        8 |     90% |54, 69-70, 139-140, 142, 153-154 |
| src/kraft/config.py                          |      563 |       18 |     97% |105, 123-125, 254, 453, 456, 537, 727-728, 820-821, 845-846, 875, 1031-1032, 1114 |
| src/kraft/config\_schemas.py                 |       73 |        7 |     90% |   160-166 |
| src/kraft/db.py                              |      142 |        4 |     97% |1143, 1169-1171 |
| src/kraft/doctor.py                          |      524 |       35 |     93% |123, 190-191, 209-210, 220-222, 234, 238-239, 278, 341-342, 381-388, 436-437, 507-508, 646-647, 700, 712-713, 852-853, 936, 979, 987, 1071-1072, 1100, 1104-1105 |
| src/kraft/drafts/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/drafts/areas.py                    |        6 |        0 |    100% |           |
| src/kraft/drafts/authored.py                 |      239 |       14 |     94% |83, 134, 158, 247, 293, 305, 310, 318, 331-332, 352, 358, 376, 381 |
| src/kraft/drafts/config.py                   |      110 |        9 |     92% |43, 87-88, 106-107, 158-159, 161-162 |
| src/kraft/drafts/harnesses.py                |      322 |       20 |     94% |140, 158, 165, 228, 239, 242, 244, 255, 257, 265, 270, 275, 279, 346, 357, 370-371, 441-443 |
| src/kraft/drafts/intake.py                   |       94 |        6 |     94% |36, 50, 109-110, 124-125 |
| src/kraft/drafts/item.py                     |       88 |        2 |     98% |   88, 116 |
| src/kraft/drafts/ops.py                      |      630 |       66 |     90% |81, 95, 134-135, 147, 154, 159, 165, 171, 177-178, 184-185, 201, 206, 289, 294-295, 350, 400, 402, 431-432, 462, 493, 513, 530, 535-536, 541, 551, 564, 576-581, 593, 618, 648, 656, 662, 682, 692, 696, 699, 726, 731, 777, 802, 820, 837, 854, 856, 860-862, 865, 868, 883, 886, 925-926, 931-932 |
| src/kraft/drafts/policy.py                   |      175 |       13 |     93% |51, 65, 82-84, 92, 99, 167, 180-181, 241, 243, 246 |
| src/kraft/drafts/policy\_caps.py             |       60 |        3 |     95% | 43, 49-50 |
| src/kraft/drafts/repos.py                    |      130 |        6 |     95% |36, 44, 49, 69, 117-118 |
| src/kraft/drafts/resolve.py                  |      420 |       24 |     94% |120, 127-128, 184-185, 207, 270-271, 286-287, 307, 317, 326, 335, 355, 424, 445, 592, 600-601, 616, 648, 680-681 |
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
| src/kraft/executor/stops.py                  |      144 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      575 |       13 |     98% |550, 667, 968, 992, 1000, 1073-1088, 1133, 1361, 1533, 1668, 1725, 1771 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       68 |        4 |     94% |102-105, 110, 186 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      335 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 404, 563, 565, 567, 576, 595-596, 633-635 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |151-152, 161-163 |
| src/kraft/index/embed.py                     |       58 |       15 |     74% |34-38, 63, 68-74, 94-96 |
| src/kraft/index/ingest.py                    |      225 |       11 |     95% |79-80, 152-153, 267, 357-359, 469-471 |
| src/kraft/index/service.py                   |      350 |       27 |     92% |140-142, 236-238, 274, 293-295, 348-349, 354, 357-358, 457, 479, 498-499, 515, 533, 537-538, 704, 707, 710, 713 |
| src/kraft/init.py                            |       51 |        4 |     92% |62-64, 125-126 |
| src/kraft/intake.py                          |      127 |       12 |     91% |89-91, 107, 150-151, 185-187, 206-211, 247 |
| src/kraft/intent.py                          |      130 |        9 |     93% |91-92, 172-186, 203, 234 |
| src/kraft/logs.py                            |      177 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 168-169, 177-178, 278 |
| src/kraft/mcp.py                             |      125 |        2 |     98% |   483-484 |
| src/kraft/mr\_poller.py                      |       66 |       12 |     82% |70-71, 83-84, 90, 104, 123-128 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      152 |       13 |     91% |183-184, 268-275, 288-291, 316-319 |
| src/kraft/overrides.py                       |       88 |        4 |     95% |36, 86, 159-160 |
| src/kraft/paths.py                           |       59 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       69 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/policy.py                          |      693 |       20 |     97% |252-253, 1053, 1240, 1474-1483, 1563, 1570, 1584, 1588-1592, 1597 |
| src/kraft/progress.py                        |      113 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       58 |        9 |     84% |159-161, 169-174 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/render.py                          |      222 |       23 |     90% |46-47, 76, 83, 322, 339, 352, 358-362, 400-407, 435, 447, 473 |
| src/kraft/review.py                          |      113 |        6 |     95% |89, 219, 223, 257, 259, 271 |
| src/kraft/review\_reply.py                   |       75 |        7 |     91% |75, 93-94, 122-123, 128, 142 |
| src/kraft/skill.py                           |       50 |        5 |     90% |77-78, 103, 127-128 |
| src/kraft/store/\_\_init\_\_.py              |       13 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       24 |        1 |     96% |        77 |
| src/kraft/store/budget.py                    |       65 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      187 |        4 |     98% |321, 409-411 |
| src/kraft/store/counters.py                  |       45 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       47 |        0 |    100% |           |
| src/kraft/store/intake.py                    |       14 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      185 |        6 |     97% |197, 202, 209-210, 224, 231 |
| src/kraft/store/sessions.py                  |      155 |        0 |    100% |           |
| src/kraft/store/work\_items.py               |      180 |        0 |    100% |           |
| src/kraft/templates/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py             |       36 |        0 |    100% |           |
| src/kraft/templates/environment.py           |      284 |        2 |     99% |  519, 521 |
| src/kraft/templates/forks.py                 |      100 |        0 |    100% |           |
| src/kraft/templates/library.py               |      402 |        4 |     99% |200, 202, 214, 221 |
| src/kraft/templates/models.py                |      690 |        7 |     99% |104, 107, 522, 700, 1281-1282, 1545 |
| src/kraft/templates/positions.py             |       57 |        0 |    100% |           |
| src/kraft/templates/retry.py                 |      105 |       11 |     90% |189, 191-197, 199-200, 207 |
| src/kraft/templates/revision.py              |      248 |        8 |     97% |216, 251, 300, 450, 491-492, 494, 508 |
| src/kraft/triggers.py                        |       52 |        6 |     88% |61-62, 101-104 |
| src/kraft/update.py                          |      141 |        9 |     94% |76-77, 107-111, 116, 177-179 |
| src/kraft/usage.py                           |      469 |       26 |     94% |115-116, 222, 353, 474, 534, 554-555, 558-559, 603, 682, 702-703, 877, 952-953, 974-975, 1072, 1080-1081, 1083, 1094, 1114-1115 |
| src/kraft/waits.py                           |      108 |        7 |     94% |286-288, 298-301 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      771 |       48 |     94% |301, 313-316, 322-323, 395, 508, 555, 564-565, 581, 628-629, 656, 659-662, 731, 929-930, 1040-1041, 1068, 1214-1215, 1263, 1354-1357, 1450-1451, 1473, 1476-1477, 1520, 1580-1581, 1622, 1700-1701, 1737-1738, 1819-1820, 1846-1847 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       301 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      159 |        7 |     96% |141, 146, 177-178, 279, 305-306 |
| src/kraft/worker/egress.py                   |      455 |       36 |     92% |289-290, 296, 337-339, 358, 394, 400, 416-417, 427, 445, 447, 507, 512, 557, 565-566, 670, 681, 696, 720-721, 723-724, 736, 739, 769-770, 820-825 |
| src/kraft/worker/env.py                      |       10 |        0 |    100% |           |
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
| **TOTAL**                                    | **26880** | **1377** | **95%** |           |


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