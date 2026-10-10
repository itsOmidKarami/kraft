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
| src/kraft/adapters/subprocess.py             |      408 |       12 |     97% |253-254, 268-269, 296, 495-496, 560, 902, 928-929, 1100 |
| src/kraft/analytics.py                       |      262 |        7 |     97% |54-55, 176, 182, 438-440 |
| src/kraft/api/\_\_init\_\_.py                |       44 |        0 |    100% |           |
| src/kraft/api/apidocs.py                     |       24 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      333 |       22 |     93% |111, 188, 203-204, 246, 282-283, 293-294, 418, 474-477, 502, 553-554, 606-607, 622-623, 625, 631, 633 |
| src/kraft/api/deps.py                        |      324 |       21 |     94% |88-89, 221-222, 432, 435-437, 509-510, 548-549, 558-559, 692-693, 728, 731, 734, 757-758 |
| src/kraft/api/perimeter.py                   |      133 |        2 |     98% |  271, 426 |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       40 |        0 |    100% |           |
| src/kraft/api/routes/artifacts.py            |      261 |        7 |     97% |152, 273-280, 563 |
| src/kraft/api/routes/auth.py                 |       64 |        0 |    100% |           |
| src/kraft/api/routes/board.py                |      424 |        3 |     99% |685, 784, 787 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      285 |        9 |     97% |248, 264, 322-323, 329, 345, 362, 401, 508 |
| src/kraft/api/routes/gates.py                |      141 |        6 |     96% |122, 125, 133, 343, 376-377 |
| src/kraft/api/routes/harnesses.py            |       97 |        1 |     99% |       253 |
| src/kraft/api/routes/lifecycle.py            |      898 |       48 |     95% |172, 175-176, 181-182, 286, 299, 383-386, 407-408, 429, 517, 594, 769, 820, 865, 885, 991-992, 1002, 1165-1166, 1285, 1317, 1326, 1406, 1500-1501, 1717, 1722-1723, 1737, 1859, 1889-1890, 1953, 1973-1974, 2019-2020, 2022, 2065, 2201-2202, 2252 |
| src/kraft/api/routes/repos.py                |      256 |        8 |     97% |99-100, 178, 197, 313-314, 445-446 |
| src/kraft/api/routes/review.py               |      328 |       15 |     95% |86, 90, 101, 164, 189, 221, 260, 262, 272, 333, 460-461, 511-512, 614 |
| src/kraft/api/routes/search.py               |      152 |       11 |     93% |48-49, 83-86, 89, 135, 148-149, 188-189, 246 |
| src/kraft/api/routes/sessions.py             |      182 |       11 |     94% |99, 146, 150, 294, 302, 377-379, 383, 385, 388 |
| src/kraft/api/routes/settings.py             |      460 |       31 |     93% |86-87, 129-130, 197-198, 213-214, 223-224, 238-239, 314, 356-357, 481-484, 487, 529-530, 555-558, 561-562, 599-600, 647-648, 663-664, 696 |
| src/kraft/api/routes/storage.py              |       46 |        0 |    100% |           |
| src/kraft/api/routes/work\_items.py          |      441 |       32 |     93% |170, 461-464, 580-598, 672, 795-796, 983, 1000, 1017-1018, 1117-1118, 1175-1176, 1181 |
| src/kraft/api/startup.py                     |      190 |        4 |     98% |110-111, 192-193 |
| src/kraft/apply.py                           |      109 |        6 |     94% |49, 69-70, 137-138, 176 |
| src/kraft/archive.py                         |       35 |        6 |     83% |     60-65 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       50 |        4 |     92% |   186-189 |
| src/kraft/automated\_review.py               |       12 |        0 |    100% |           |
| src/kraft/builtins.py                        |      745 |       47 |     94% |114-115, 123, 214, 280, 364, 378, 412, 416, 420, 426, 497-498, 518-519, 606, 791-792, 924, 940, 996-997, 1096-1097, 1269, 1386, 1421-1423, 1441-1442, 1522, 1528-1531, 1587-1588, 1600-1601, 1644, 1652-1653, 1696-1697, 2100-2103 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      294 |       16 |     95% |141, 360, 386, 397, 409-410, 423, 520, 592, 630-632, 676-679 |
| src/kraft/cli/\_\_init\_\_.py                |       56 |       12 |     79% |95-101, 134-141, 148 |
| src/kraft/cli/admin.py                       |      740 |       43 |     94% |87, 170-172, 190, 196-200, 209, 251, 287-288, 383-384, 392, 406, 521, 640, 667, 797-798, 971-974, 1122-1123, 1241-1242, 1247, 1250, 1259-1265, 1304-1305, 1485, 1511 |
| src/kraft/cli/common.py                      |       41 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      389 |       12 |     97% |32, 107, 159, 162, 194, 266, 418, 441, 478-479, 492, 551 |
| src/kraft/cli/repo.py                        |      294 |       11 |     96% |139, 147, 162, 175-176, 231, 233, 333, 364, 452-453 |
| src/kraft/cli/templates.py                   |       71 |        1 |     99% |        98 |
| src/kraft/cli/verify.py                      |      154 |        5 |     97% |133, 211-214 |
| src/kraft/cli/view.py                        |      297 |       10 |     97% |82, 273-274, 296, 306, 356-357, 393-399 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      207 |       25 |     88% |64, 167, 184, 203, 271, 295-296, 335, 357, 359, 385, 392-396, 474-475, 497, 523, 535, 570, 585, 609, 671 |
| src/kraft/client/context.py                  |       49 |        2 |     96% |     44-45 |
| src/kraft/client/reads.py                    |      169 |       10 |     94% |246-247, 257-264, 329, 352, 358, 404 |
| src/kraft/client/transport.py                |       95 |        9 |     91% |54, 69-70, 87, 177-178, 180, 191-192 |
| src/kraft/config.py                          |      647 |       13 |     98% |105, 124, 181-183, 328, 572, 795, 839-840, 1056-1057, 1144 |
| src/kraft/config\_schemas.py                 |       74 |        7 |     91% |   162-168 |
| src/kraft/db.py                              |      148 |        4 |     97% |1322, 1348-1350 |
| src/kraft/detect.py                          |     1085 |       64 |     94% |202, 357-359, 366-368, 455-462, 483, 578-579, 593, 601, 615, 635, 680, 686, 900, 913, 931, 949, 951, 1012-1013, 1087-1088, 1102, 1178, 1181, 1260, 1272, 1285, 1314-1316, 1326, 1467-1468, 1579, 1669-1670, 1672, 1845-1863, 1950, 2017 |
| src/kraft/doctor.py                          |      643 |       37 |     94% |154, 282-283, 361-362, 416, 420, 438, 442-443, 485, 548-549, 588-595, 643-644, 714-715, 853-854, 907, 919-920, 1069-1070, 1153, 1171, 1184, 1194, 1236, 1244, 1340-1341, 1369, 1373-1374 |
| src/kraft/drafts/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/drafts/areas.py                    |        6 |        0 |    100% |           |
| src/kraft/drafts/authored.py                 |      239 |       14 |     94% |83, 134, 158, 247, 293, 305, 310, 318, 331-332, 352, 358, 376, 381 |
| src/kraft/drafts/config.py                   |      114 |        9 |     92% |47, 102-103, 121-122, 173-174, 176-177 |
| src/kraft/drafts/harnesses.py                |      324 |       21 |     94% |141, 159, 166, 240, 243, 245, 256, 258, 266, 270-273, 276, 280, 347, 358, 371-372, 442-444 |
| src/kraft/drafts/intake.py                   |       97 |        4 |     96% |45, 104-105, 140 |
| src/kraft/drafts/item.py                     |       88 |        2 |     98% |   88, 116 |
| src/kraft/drafts/ops.py                      |      632 |       66 |     90% |81, 95, 134-135, 147, 154, 159, 165, 171, 177-178, 184-185, 201, 206, 289, 294-295, 350, 400, 402, 431-432, 462, 493, 513, 530, 535-536, 541, 551, 564, 576-581, 593, 618, 648, 656, 662, 682, 695, 699, 702, 729, 734, 780, 805, 823, 840, 857, 859, 863-865, 868, 871, 886, 889, 928-929, 934-935 |
| src/kraft/drafts/policy.py                   |      178 |       13 |     93% |57, 71, 88-90, 98, 105, 173, 186-187, 247, 249, 252 |
| src/kraft/drafts/policy\_caps.py             |       60 |        3 |     95% | 43, 49-50 |
| src/kraft/drafts/preserve.py                 |      281 |       22 |     92% |142-143, 178-179, 182, 300-314, 423, 426-427, 469 |
| src/kraft/drafts/repos.py                    |      139 |        6 |     96% |37, 45, 50, 78, 126-127 |
| src/kraft/drafts/resolve.py                  |      425 |       24 |     94% |121, 128-129, 185-186, 208, 302-303, 318-319, 339, 349, 358, 367, 387, 456, 477, 624, 632-633, 648, 680, 712-713 |
| src/kraft/drafts/store.py                    |       78 |        0 |    100% |           |
| src/kraft/escalate.py                        |      182 |        6 |     97% |190, 293, 414, 594-605, 655 |
| src/kraft/events.py                          |       31 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       55 |        2 |     96% |  216, 247 |
| src/kraft/executor/dispatch.py               |      821 |       19 |     98% |271, 338, 360-361, 447, 471, 616, 619, 898-899, 1054-1055, 1276-1277, 1654, 1662, 2369, 2722, 2728 |
| src/kraft/executor/entry.py                  |      141 |        1 |     99% |       349 |
| src/kraft/executor/fallback.py               |       83 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      327 |       11 |     97% |96, 237, 357, 360, 363, 366, 747, 764, 878-879, 1189 |
| src/kraft/executor/prompts.py                |      199 |        6 |     97% |146-152, 239, 672 |
| src/kraft/executor/read\_only.py             |       75 |        2 |     97% |    69, 82 |
| src/kraft/executor/resuming.py               |       83 |        1 |     99% |       169 |
| src/kraft/executor/retry.py                  |       26 |        0 |    100% |           |
| src/kraft/executor/stops.py                  |      147 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      624 |       13 |     98% |670, 789, 1090, 1117, 1129, 1202-1217, 1262, 1499, 1676, 1831, 1888, 1934 |
| src/kraft/findings.py                        |      111 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |       78 |        4 |     95% |100-103, 108, 184 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      335 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 404, 563, 565, 567, 576, 595-596, 633-635 |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |151-152, 161-163 |
| src/kraft/index/embed.py                     |       63 |       13 |     79% |37-38, 68, 73, 78-84, 104-106 |
| src/kraft/index/ingest.py                    |      225 |        9 |     96% |152-153, 267, 357-359, 469-471 |
| src/kraft/index/service.py                   |      383 |       26 |     93% |141-143, 237-239, 275, 294-296, 350, 355, 358-359, 481, 486, 517-518, 534, 552, 556-557, 744, 747, 753, 815 |
| src/kraft/init.py                            |       51 |        2 |     96% |     62-64 |
| src/kraft/intake.py                          |      130 |       12 |     91% |98-100, 116, 159-160, 194-196, 215-220, 256 |
| src/kraft/intent.py                          |      198 |        9 |     95% |91-92, 186, 203, 264-265, 321-322, 339 |
| src/kraft/logs.py                            |      177 |       12 |     93% |61-62, 103, 108, 125, 135, 152, 168-169, 177-178, 278 |
| src/kraft/mcp.py                             |      132 |        2 |     98% |   550-551 |
| src/kraft/mr\_poller.py                      |       68 |        9 |     87% |72-73, 85-86, 92, 127-130 |
| src/kraft/node\_runs.py                      |       64 |        1 |     98% |        55 |
| src/kraft/notify.py                          |      153 |       10 |     93% |184-185, 269-276, 289-292, 318 |
| src/kraft/overrides.py                       |       98 |        3 |     97% |116, 189-190 |
| src/kraft/paths.py                           |       96 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       70 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/pidfile.py                         |      125 |       19 |     85% |51-52, 57-58, 80-81, 117, 134, 145-146, 160-161, 169-170, 172, 196-197, 199-200 |
| src/kraft/policy.py                          |      777 |       24 |     97% |156, 276-277, 578, 583-584, 1245, 1432, 1666-1675, 1755, 1762, 1776, 1780-1784, 1789 |
| src/kraft/progress.py                        |      148 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       59 |        7 |     88% |160-162, 172-175 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/render.py                          |      274 |       24 |     91% |59-60, 97, 104, 382, 393, 408, 421, 427-431, 469-476, 527, 539, 565 |
| src/kraft/review.py                          |      183 |        5 |     97% |99, 229, 233, 281, 334 |
| src/kraft/review\_reply.py                   |       81 |        7 |     91% |77, 99-100, 132-133, 138, 158 |
| src/kraft/skill.py                           |       50 |        5 |     90% |77-78, 103, 127-128 |
| src/kraft/start\_queue.py                    |       77 |        5 |     94% |105, 170-173 |
| src/kraft/storage.py                         |      139 |        7 |     95% |71-72, 172, 210-213 |
| src/kraft/store/\_\_init\_\_.py              |       13 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       46 |        2 |     96% |  126, 143 |
| src/kraft/store/budget.py                    |       69 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      199 |        4 |     98% |354, 444-446 |
| src/kraft/store/counters.py                  |       55 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       44 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       52 |        0 |    100% |           |
| src/kraft/store/intake.py                    |       14 |        0 |    100% |           |
| src/kraft/store/passes.py                    |       56 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      181 |        4 |     98% |212, 217, 224-225 |
| src/kraft/store/sessions.py                  |      161 |        0 |    100% |           |
| src/kraft/store/work\_items.py               |      271 |        4 |     99% |381, 416, 534, 541 |
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
| src/kraft/vocab/\_\_init\_\_.py              |        7 |        0 |    100% |           |
| src/kraft/vocab/display.py                   |       14 |        0 |    100% |           |
| src/kraft/vocab/events/\_\_init\_\_.py       |       32 |        0 |    100% |           |
| src/kraft/vocab/events/chain.py              |       29 |        0 |    100% |           |
| src/kraft/vocab/events/escalation.py         |       15 |        0 |    100% |           |
| src/kraft/vocab/events/forge.py              |       21 |        0 |    100% |           |
| src/kraft/vocab/events/gate.py               |       26 |        0 |    100% |           |
| src/kraft/vocab/events/limit.py              |       17 |        0 |    100% |           |
| src/kraft/vocab/events/sandbox.py            |       12 |        0 |    100% |           |
| src/kraft/vocab/events/session.py            |       17 |        0 |    100% |           |
| src/kraft/vocab/events/settings.py           |       14 |        0 |    100% |           |
| src/kraft/vocab/events/traits.py             |        8 |        0 |    100% |           |
| src/kraft/vocab/events/work\_item.py         |       35 |        0 |    100% |           |
| src/kraft/vocab/review.py                    |       22 |        0 |    100% |           |
| src/kraft/vocab/session.py                   |       42 |        0 |    100% |           |
| src/kraft/vocab/sql.py                       |       29 |        0 |    100% |           |
| src/kraft/vocab/stop.py                      |       26 |        0 |    100% |           |
| src/kraft/vocab/total.py                     |       11 |        0 |    100% |           |
| src/kraft/vocab/work\_item.py                |       54 |        0 |    100% |           |
| src/kraft/waits.py                           |      106 |        7 |     93% |296-298, 308-311 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       35 |        0 |    100% |           |
| src/kraft/worker/backends/docker.py          |      799 |       54 |     93% |324, 336-339, 351-358, 435, 550, 598, 607-608, 626, 656, 681-682, 709, 712-715, 784, 987-988, 1103-1104, 1131, 1277-1278, 1326, 1417-1420, 1513-1514, 1539-1540, 1583, 1643-1644, 1685, 1763-1764, 1800-1801, 1882-1883, 1909-1910 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       301 |
| src/kraft/worker/callback.py                 |       33 |        1 |     97% |        41 |
| src/kraft/worker/channel.py                  |      160 |        7 |     96% |142, 147, 178-179, 280, 306-307 |
| src/kraft/worker/egress.py                   |      463 |       35 |     92% |286-287, 293, 334-336, 355, 391, 397, 413-414, 424, 442, 444, 504, 561, 569-570, 674, 685, 708, 733-734, 736-737, 749, 752, 782-783, 833-838 |
| src/kraft/worker/env.py                      |       11 |        0 |    100% |           |
| src/kraft/worker/inject.py                   |      238 |       18 |     92% |194, 214, 230, 241, 253, 351, 365, 374, 384, 390, 393, 407, 410, 415, 424-427 |
| src/kraft/worker/kit.py                      |      421 |       10 |     98% |124, 238-240, 430, 669-670, 672, 698, 732 |
| src/kraft/worker/reattach.py                 |      304 |       26 |     91% |102-103, 112-113, 121-122, 155-156, 256, 302, 314-315, 346, 398, 406, 420-421, 571, 581-582, 649-650, 663-664, 677-678 |
| src/kraft/worker/refstore.py                 |      224 |        7 |     97% |187, 190, 306, 394-395, 423, 446 |
| src/kraft/worker/sandbox.py                  |      158 |        7 |     96% |109, 147-148, 153-154, 191, 216 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |44, 49-50, 52 |
| src/kraft/worker/shim.py                     |        4 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       37 |        0 |    100% |           |
| src/kraft/worker/worktree\_read.py           |       82 |        9 |     89% |45, 96, 142-144, 147-151, 159-160 |
| src/kraft/ws.py                              |       72 |        3 |     96% |     77-79 |
| **TOTAL**                                    | **32211** | **1470** | **95%** |           |


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