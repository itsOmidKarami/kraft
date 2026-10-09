# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                    |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py                  |      270 |       11 |     96% |340, 394-395, 397, 610, 714-717, 948-949 |
| src/kraft/adapters/artifact\_notes.py        |        2 |        0 |    100% |           |
| src/kraft/adapters/beads.py                  |       62 |       19 |     69% |82-88, 122, 125-126, 182-190 |
| src/kraft/adapters/forge/\_\_init\_\_.py     |        8 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py               |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/gh.py               |      156 |       12 |     92% |172, 252, 276, 282-292, 370, 421 |
| src/kraft/adapters/forge/git.py              |      215 |       15 |     93% |216-217, 234-235, 258-259, 291-293, 357-359, 554-561, 739 |
| src/kraft/adapters/forge/glab.py             |      180 |       10 |     94% |239-240, 327, 336, 359-360, 426, 429, 442, 464 |
| src/kraft/adapters/forge/models.py           |      179 |        0 |    100% |           |
| src/kraft/adapters/forge/mr.py               |      124 |        5 |     96% |74, 197, 249, 315-316 |
| src/kraft/adapters/forge/run.py              |      451 |       11 |     98% |427, 611-613, 856-857, 887-898, 1400, 1494 |
| src/kraft/adapters/hook\_install.py          |      232 |       10 |     96% |91, 185-188, 264-265, 290-291, 380 |
| src/kraft/adapters/profiles.py               |       32 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      410 |       12 |     97% |244-245, 259-260, 287, 488-489, 553, 895, 921-922, 1093 |
| src/kraft/analytics.py                       |      263 |        7 |     97% |54-55, 193, 199, 455-457 |
| src/kraft/api/\_\_init\_\_.py                |       44 |        0 |    100% |           |
| src/kraft/api/apidocs.py                     |       24 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      329 |       23 |     93% |111, 188, 203-204, 246, 282-283, 293-294, 391, 410, 466-469, 494, 545-546, 598-599, 614-615, 617, 623, 625 |
| src/kraft/api/deps.py                        |      323 |       21 |     93% |88-89, 216-217, 427, 430-432, 504-505, 543-544, 553-554, 687-688, 723, 726, 729, 752-753 |
| src/kraft/api/perimeter.py                   |      133 |        2 |     98% |  271, 426 |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       40 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py            |      253 |        7 |     97% |149, 248-255, 534 |
| src/kraft/api/routes/auth.py                 |       61 |        0 |    100% |           |
| src/kraft/api/routes/board.py                |      415 |        3 |     99% |670, 769, 772 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      285 |        9 |     97% |248, 264, 322-323, 329, 345, 362, 401, 508 |
| src/kraft/api/routes/gates.py                |      141 |        6 |     96% |119, 122, 130, 340, 373-374 |
| src/kraft/api/routes/harnesses.py            |       97 |        1 |     99% |       253 |
| src/kraft/api/routes/lifecycle.py            |      837 |       44 |     95% |154, 157-158, 163-164, 268, 281, 365-368, 389-390, 411, 497, 570, 700, 737, 858-859, 869, 1028-1029, 1173, 1182, 1350-1351, 1567, 1572-1573, 1587, 1707, 1737-1738, 1801, 1821-1822, 1867-1868, 1870, 1911, 2047-2048, 2098 |
| src/kraft/api/routes/repos.py                |      256 |        8 |     97% |99-100, 178, 197, 313-314, 445-446 |
| src/kraft/api/routes/review.py               |      329 |       15 |     95% |79, 83, 94, 157, 182, 214, 253, 255, 265, 326, 439-440, 485-486, 588 |
| src/kraft/api/routes/search.py               |      152 |       11 |     93% |48-49, 83-86, 89, 135, 148-149, 188-189, 246 |
| src/kraft/api/routes/sessions.py             |      182 |       11 |     94% |99, 146, 150, 294, 302, 377-379, 383, 385, 388 |
| src/kraft/api/routes/settings.py             |      455 |       31 |     93% |86-87, 129-130, 197-198, 213-214, 223-224, 238-239, 314, 356-357, 481-484, 487, 528-529, 554-557, 560-561, 598-599, 646-647, 662-663, 695 |
| src/kraft/api/routes/work\_items.py          |      429 |       32 |     93% |166, 444-447, 549-567, 641, 764-765, 952, 969, 986-987, 1086-1087, 1144-1145, 1150 |
| src/kraft/api/startup.py                     |      181 |        4 |     98% |109-110, 187-188 |
| src/kraft/apply.py                           |      107 |        6 |     94% |48, 68-69, 136-137, 172 |
| src/kraft/archive.py                         |       35 |        6 |     83% |     60-65 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       50 |        4 |     92% |   186-189 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      744 |       47 |     94% |113-114, 122, 213, 279, 363, 377, 411, 415, 419, 425, 496-497, 517-518, 605, 790-791, 923, 939, 995-996, 1095-1096, 1268, 1385, 1420-1422, 1440-1441, 1521, 1527-1530, 1586-1587, 1599-1600, 1643, 1651-1652, 1695-1696, 2099-2102 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      294 |       16 |     95% |130, 338, 364, 375, 387-388, 401, 498, 569, 614-616, 658-661 |
| src/kraft/cli/\_\_init\_\_.py                |       56 |       12 |     79% |95-101, 134-141, 148 |
| src/kraft/cli/admin.py                       |      740 |       43 |     94% |87, 170-172, 190, 196-200, 209, 251, 287-288, 383-384, 392, 406, 521, 640, 667, 797-798, 971-974, 1122-1123, 1241-1242, 1247, 1250, 1259-1265, 1304-1305, 1485, 1511 |
| src/kraft/cli/common.py                      |       41 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      325 |       10 |     97% |31, 105, 178, 186, 322, 345, 382-383, 395, 454 |
| src/kraft/cli/repo.py                        |      294 |       11 |     96% |139, 147, 162, 175-176, 231, 233, 333, 364, 452-453 |
| src/kraft/cli/templates.py                   |       71 |        1 |     99% |        98 |
| src/kraft/cli/verify.py                      |      154 |        5 |     97% |133, 211-214 |
| src/kraft/cli/view.py                        |      286 |       10 |     97% |78, 262-263, 285, 295, 345-346, 382-388 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      196 |       25 |     87% |62, 164, 181, 200, 268, 282-283, 301, 323, 325, 351, 358-362, 440-441, 463, 489, 501, 536, 551, 575, 637 |
| src/kraft/client/context.py                  |       49 |        2 |     96% |     44-45 |
| src/kraft/client/reads.py                    |      167 |       10 |     94% |242-243, 253-260, 325, 348, 354, 400 |
| src/kraft/client/transport.py                |       95 |        9 |     91% |54, 69-70, 87, 177-178, 180, 191-192 |
| src/kraft/config.py                          |      632 |       13 |     98% |104, 123, 180-182, 327, 571, 794, 838-839, 1055-1056, 1143 |
| src/kraft/config\_schemas.py                 |       74 |        7 |     91% |   162-168 |
| src/kraft/db.py                              |      146 |        4 |     97% |1240, 1266-1268 |
| src/kraft/detect.py                          |     1085 |       64 |     94% |202, 357-359, 366-368, 455-462, 483, 578-579, 593, 601, 615, 635, 680, 686, 900, 913, 931, 949, 951, 1012-1013, 1087-1088, 1102, 1178, 1181, 1260, 1272, 1285, 1314-1316, 1326, 1467-1468, 1579, 1669-1670, 1672, 1845-1863, 1950, 2017 |
| src/kraft/doctor.py                          |      615 |       37 |     94% |138, 257-258, 335-336, 368, 372, 390, 394-395, 437, 500-501, 540-547, 595-596, 666-667, 805-806, 859, 871-872, 1021-1022, 1105, 1123, 1136, 1146, 1188, 1196, 1292-1293, 1321, 1325-1326 |
| src/kraft/drafts/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/drafts/areas.py                    |        6 |        0 |    100% |           |
| src/kraft/drafts/authored.py                 |      239 |       14 |     94% |83, 134, 158, 247, 293, 305, 310, 318, 331-332, 352, 358, 376, 381 |
| src/kraft/drafts/config.py                   |      114 |        9 |     92% |47, 102-103, 121-122, 173-174, 176-177 |
| src/kraft/drafts/harnesses.py                |      322 |       21 |     93% |140, 158, 165, 239, 242, 244, 255, 257, 265, 269-272, 275, 279, 346, 357, 370-371, 441-443 |
| src/kraft/drafts/intake.py                   |       97 |        4 |     96% |45, 104-105, 140 |
| src/kraft/drafts/item.py                     |       88 |        2 |     98% |   88, 116 |
| src/kraft/drafts/ops.py                      |      632 |       66 |     90% |81, 95, 134-135, 147, 154, 159, 165, 171, 177-178, 184-185, 201, 206, 289, 294-295, 350, 400, 402, 431-432, 462, 493, 513, 530, 535-536, 541, 551, 564, 576-581, 593, 618, 648, 656, 662, 682, 695, 699, 702, 729, 734, 780, 805, 823, 840, 857, 859, 863-865, 868, 871, 886, 889, 928-929, 934-935 |
| src/kraft/drafts/policy.py                   |      175 |       13 |     93% |51, 65, 82-84, 92, 99, 167, 180-181, 241, 243, 246 |
| src/kraft/drafts/policy\_caps.py             |       60 |        3 |     95% | 43, 49-50 |
| src/kraft/drafts/preserve.py                 |      281 |       22 |     92% |142-143, 178-179, 182, 300-314, 423, 426-427, 469 |
| src/kraft/drafts/repos.py                    |      139 |        6 |     96% |37, 45, 50, 78, 126-127 |
| src/kraft/drafts/resolve.py                  |      425 |       24 |     94% |121, 128-129, 185-186, 208, 302-303, 318-319, 339, 349, 358, 367, 387, 456, 477, 624, 632-633, 648, 680, 712-713 |
| src/kraft/drafts/store.py                    |       78 |        0 |    100% |           |
| src/kraft/escalate.py                        |      182 |        6 |     97% |190, 293, 412, 592-603, 653 |
| src/kraft/events.py                          |       28 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       55 |        2 |     96% |  216, 247 |
| src/kraft/executor/dispatch.py               |      811 |       19 |     98% |261, 328, 350-351, 437, 461, 606, 609, 887-888, 1043-1044, 1265-1266, 1643, 1651, 2301, 2652, 2658 |
| src/kraft/executor/entry.py                  |      141 |        1 |     99% |       347 |
| src/kraft/executor/fallback.py               |       82 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      328 |       11 |     97% |96, 224, 339, 342, 345, 348, 727, 809, 862-863, 1168 |
| src/kraft/executor/prompts.py                |      199 |        6 |     97% |146-152, 239, 672 |
| src/kraft/executor/read\_only.py             |       76 |        2 |     97% |    71, 84 |
| src/kraft/executor/resuming.py               |       83 |        1 |     99% |       169 |
| src/kraft/executor/retry.py                  |       25 |        0 |    100% |           |
| src/kraft/executor/stops.py                  |      147 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      623 |       13 |     98% |661, 780, 1081, 1108, 1120, 1193-1208, 1253, 1485, 1658, 1811, 1868, 1914 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       78 |        4 |     95% |100-103, 108, 184 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      335 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 404, 563, 565, 567, 576, 595-596, 633-635 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |151-152, 161-163 |
| src/kraft/index/embed.py                     |       63 |       13 |     79% |37-38, 68, 73, 78-84, 104-106 |
| src/kraft/index/ingest.py                    |      225 |        9 |     96% |152-153, 267, 357-359, 469-471 |
| src/kraft/index/service.py                   |      382 |       27 |     93% |140-142, 236-238, 274, 293-295, 348-349, 354, 357-358, 480, 485, 516-517, 533, 551, 555-556, 743, 746, 752, 814 |
| src/kraft/init.py                            |       51 |        2 |     96% |     62-64 |
| src/kraft/intake.py                          |      127 |       12 |     91% |94-96, 112, 155-156, 190-192, 211-216, 252 |
| src/kraft/intent.py                          |      198 |        9 |     95% |91-92, 186, 203, 264-265, 321-322, 339 |
| src/kraft/logs.py                            |      177 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 168-169, 177-178, 278 |
| src/kraft/mcp.py                             |      129 |        2 |     98% |   533-534 |
| src/kraft/mr\_poller.py                      |       68 |        9 |     87% |72-73, 85-86, 92, 127-130 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      152 |       10 |     93% |183-184, 268-275, 288-291, 317 |
| src/kraft/overrides.py                       |       98 |        3 |     97% |116, 189-190 |
| src/kraft/paths.py                           |       96 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       70 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/pidfile.py                         |      125 |       19 |     85% |51-52, 57-58, 80-81, 117, 134, 145-146, 160-161, 169-170, 172, 196-197, 199-200 |
| src/kraft/policy.py                          |      742 |       23 |     97% |238-239, 523, 528-529, 1175, 1362, 1596-1605, 1685, 1692, 1706, 1710-1714, 1719 |
| src/kraft/progress.py                        |      148 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       59 |        7 |     88% |160-162, 172-175 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/render.py                          |      251 |       24 |     90% |58-59, 88, 95, 373, 384, 399, 412, 418-422, 460-467, 508, 520, 546 |
| src/kraft/review.py                          |      178 |        5 |     97% |89, 219, 223, 271, 324 |
| src/kraft/review\_reply.py                   |       80 |        7 |     91% |76, 94-95, 123-124, 129, 149 |
| src/kraft/skill.py                           |       50 |        5 |     90% |77-78, 103, 127-128 |
| src/kraft/start\_queue.py                    |       50 |        5 |     90% |71, 108-111 |
| src/kraft/store/\_\_init\_\_.py              |       12 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       31 |        2 |     94% |    78, 95 |
| src/kraft/store/budget.py                    |       65 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      193 |        4 |     98% |321, 409-411 |
| src/kraft/store/counters.py                  |       47 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       47 |        0 |    100% |           |
| src/kraft/store/intake.py                    |       14 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      183 |        4 |     98% |217, 222, 229-230 |
| src/kraft/store/sessions.py                  |      162 |        0 |    100% |           |
| src/kraft/store/work\_items.py               |      214 |        4 |     98% |360, 387, 394, 405 |
| src/kraft/templates/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py             |       40 |        0 |    100% |           |
| src/kraft/templates/environment.py           |      284 |        2 |     99% |  519, 521 |
| src/kraft/templates/forks.py                 |      100 |        0 |    100% |           |
| src/kraft/templates/library.py               |      402 |        4 |     99% |200, 202, 214, 221 |
| src/kraft/templates/models.py                |      702 |        7 |     99% |104, 107, 561, 739, 1320-1321, 1584 |
| src/kraft/templates/positions.py             |       57 |        0 |    100% |           |
| src/kraft/templates/retry.py                 |      105 |       11 |     90% |189, 191-197, 199-200, 207 |
| src/kraft/templates/revision.py              |      248 |        8 |     97% |216, 251, 300, 450, 491-492, 494, 508 |
| src/kraft/triggers.py                        |       75 |        6 |     92% |98-99, 143-146 |
| src/kraft/update.py                          |      250 |       12 |     95% |80-81, 111-115, 120, 205-208, 363-364 |
| src/kraft/usage.py                           |      484 |       26 |     95% |117-118, 224, 355, 476, 536, 556-557, 560-561, 605, 684, 704-705, 879, 976-977, 1074, 1082-1083, 1085, 1096, 1116-1117, 1266, 1278 |
| src/kraft/vocab/\_\_init\_\_.py              |        5 |        0 |    100% |           |
| src/kraft/vocab/display.py                   |       13 |        0 |    100% |           |
| src/kraft/vocab/session.py                   |       42 |        0 |    100% |           |
| src/kraft/vocab/sql.py                       |       29 |        0 |    100% |           |
| src/kraft/vocab/stop.py                      |       26 |        0 |    100% |           |
| src/kraft/vocab/total.py                     |       11 |        0 |    100% |           |
| src/kraft/vocab/work\_item.py                |       52 |        0 |    100% |           |
| src/kraft/waits.py                           |      109 |        7 |     94% |287-289, 299-302 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      799 |       54 |     93% |324, 336-339, 351-358, 435, 550, 598, 607-608, 626, 656, 681-682, 709, 712-715, 784, 987-988, 1103-1104, 1131, 1277-1278, 1326, 1417-1420, 1513-1514, 1539-1540, 1583, 1643-1644, 1685, 1763-1764, 1800-1801, 1882-1883, 1909-1910 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       301 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      159 |        7 |     96% |141, 146, 177-178, 279, 305-306 |
| src/kraft/worker/egress.py                   |      464 |       35 |     92% |289-290, 296, 337-339, 358, 394, 400, 416-417, 427, 445, 447, 507, 564, 572-573, 677, 688, 711, 736-737, 739-740, 752, 755, 785-786, 836-841 |
| src/kraft/worker/env.py                      |       11 |        0 |    100% |           |
| src/kraft/worker/inject.py                   |      238 |       18 |     92% |194, 214, 230, 241, 253, 351, 365, 374, 384, 390, 393, 407, 410, 415, 424-427 |
| src/kraft/worker/kit.py                      |      421 |       10 |     98% |124, 238-240, 430, 669-670, 672, 698, 732 |
| src/kraft/worker/reattach.py                 |      304 |       26 |     91% |92-93, 102-103, 111-112, 145-146, 245, 291, 303-304, 335, 387, 395, 409-410, 560, 570-571, 638-639, 652-653, 666-667 |
| src/kraft/worker/refstore.py                 |      224 |        7 |     97% |187, 190, 306, 394-395, 423, 446 |
| src/kraft/worker/sandbox.py                  |      158 |        7 |     96% |109, 147-148, 153-154, 191, 216 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |44, 49-50, 52 |
| src/kraft/worker/shim.py                     |        4 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       37 |        0 |    100% |           |
| src/kraft/worker/worktree\_read.py           |       82 |        9 |     89% |45, 96, 142-144, 147-151, 159-160 |
| src/kraft/ws.py                              |       72 |        3 |     96% |     77-79 |
| **TOTAL**                                    | **31264** | **1458** | **95%** |           |


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