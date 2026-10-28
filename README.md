# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                    |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py                  |      402 |       21 |     95% |425, 524-525, 527, 783, 854-857, 933-936, 1105, 1279-1280, 1312-1318 |
| src/kraft/adapters/artifact\_notes.py        |        2 |        0 |    100% |           |
| src/kraft/adapters/beads.py                  |       64 |       20 |     69% |61, 94-100, 134, 137-138, 194-202 |
| src/kraft/adapters/forge/\_\_init\_\_.py     |        8 |        0 |    100% |           |
| src/kraft/adapters/forge/ci.py               |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/gh.py               |      348 |       19 |     95% |465, 511-512, 594-596, 685, 755, 786-787, 813-814, 873, 953-954, 956, 989, 1014-1015 |
| src/kraft/adapters/forge/git.py              |      239 |       15 |     94% |217-218, 235-236, 259-260, 292-294, 358-360, 555-562, 843 |
| src/kraft/adapters/forge/glab.py             |      412 |       20 |     95% |379-380, 545, 554, 577-578, 670, 726, 748, 811-812, 851-852, 854, 902, 921, 992-993, 1063-1064 |
| src/kraft/adapters/forge/models.py           |      272 |        1 |     99% |       733 |
| src/kraft/adapters/forge/mr.py               |      147 |        3 |     98% |77, 206, 265 |
| src/kraft/adapters/forge/run.py              |      738 |       38 |     95% |255-259, 315, 399-401, 419, 562-566, 704, 974, 1143-1144, 1205-1207, 1237, 1341, 1360-1361, 1373, 1583-1584, 1619-1630, 2241, 2343-2349, 2369 |
| src/kraft/adapters/forge/settle.py           |       51 |        0 |    100% |           |
| src/kraft/adapters/hook\_install.py          |      232 |       10 |     96% |91, 186-189, 265-266, 291-292, 384 |
| src/kraft/adapters/profiles.py               |       35 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      625 |       19 |     97% |135, 309-310, 594, 669, 710-711, 772-773, 952-953, 1397, 1399, 1410, 1534-1535, 1579-1580, 1697 |
| src/kraft/addons/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/addons/config.py                   |      153 |        3 |     98% |152, 154, 166 |
| src/kraft/addons/fetch.py                    |      326 |       16 |     95% |100-101, 196, 222-223, 265, 269, 408, 432, 524-525, 530-535 |
| src/kraft/addons/flow.py                     |      105 |        6 |     94% |89, 96, 98, 100, 136-137 |
| src/kraft/addons/inherit.py                  |      345 |        7 |     98% |66-67, 340, 427, 507, 514, 629 |
| src/kraft/addons/load.py                     |      472 |       39 |     92% |109, 204, 272-273, 412-413, 469-470, 505-506, 543-544, 572-573, 732-733, 762-763, 785-786, 807, 814-815, 865-866, 890, 895, 917-918, 920, 923-924, 926, 933-934, 950, 997, 1003-1004 |
| src/kraft/addons/manifest.py                 |      206 |        9 |     96% |122, 142, 148-150, 221, 258, 311-312 |
| src/kraft/addons/repo\_library.py            |      378 |       22 |     94% |88-89, 109, 115, 131-132, 225-226, 299-300, 314, 327, 345, 409-410, 429-430, 604-605, 655-656, 660 |
| src/kraft/addons/review.py                   |      298 |        9 |     97% |146-147, 194-195, 258, 387-390 |
| src/kraft/addons/update.py                   |      918 |       35 |     96% |107, 311-312, 330-331, 359-360, 383-384, 532-533, 579, 618-619, 657-658, 797, 839-840, 860-861, 1018-1019, 1082, 1088, 1276-1278, 1357-1358, 1560, 1577, 1586, 1676, 1814 |
| src/kraft/analytics.py                       |      275 |        7 |     97% |54-55, 176, 182, 471-473 |
| src/kraft/answers.py                         |      128 |        3 |     98% |206, 209, 288 |
| src/kraft/api/\_\_init\_\_.py                |       45 |        0 |    100% |           |
| src/kraft/api/apidocs.py                     |       24 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      462 |       32 |     93% |130, 207, 222-223, 297, 333-334, 344-345, 371, 374-375, 448-449, 476, 515, 571-574, 599, 682-683, 740-741, 758-759, 761, 767, 793-794, 904-905, 924 |
| src/kraft/api/deps.py                        |      506 |       27 |     95% |91-92, 189, 260-261, 556, 748, 751-753, 826-827, 855-856, 894-895, 904-905, 914-915, 1054-1055, 1090, 1093, 1096, 1119-1120 |
| src/kraft/api/item\_ids.py                   |       26 |        0 |    100% |           |
| src/kraft/api/perimeter.py                   |      133 |        2 |     98% |  271, 426 |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       88 |        1 |     99% |       134 |
| src/kraft/api/routes/artifacts.py            |      275 |       10 |     96% |159, 296-303, 350-351, 412, 597 |
| src/kraft/api/routes/auth.py                 |       65 |        0 |    100% |           |
| src/kraft/api/routes/board.py                |      493 |        3 |     99% |920, 1019, 1022 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      300 |        9 |     97% |273, 289, 347-348, 354, 370, 387, 431, 542 |
| src/kraft/api/routes/gates.py                |      187 |        7 |     96% |134, 137, 151, 409-410, 494-495 |
| src/kraft/api/routes/harnesses.py            |       98 |        1 |     99% |       260 |
| src/kraft/api/routes/lifecycle.py            |     1142 |       58 |     95% |185, 188-189, 194-195, 255, 271, 364, 377, 461-464, 485-486, 507, 653, 741, 924, 987, 1002, 1033-1044, 1080-1081, 1176, 1196, 1307-1308, 1318, 1492-1493, 1536, 1618, 1620, 1665, 1783, 1815, 1824, 1904, 2002-2003, 2076, 2242, 2247-2248, 2264, 2363, 2438-2439, 2524-2525, 2575-2576, 2578, 2787-2788, 2846 |
| src/kraft/api/routes/repos.py                |      342 |       16 |     95% |106-107, 226, 346-347, 363, 378-379, 447-448, 588-589, 713-714, 739-740 |
| src/kraft/api/routes/review.py               |      359 |       14 |     96% |86, 90, 101, 164, 189, 221, 260, 262, 272, 333, 519, 564-565, 661 |
| src/kraft/api/routes/search.py               |      158 |       11 |     93% |49-50, 84-87, 90, 136, 149-150, 189-190, 251 |
| src/kraft/api/routes/sessions.py             |      197 |       10 |     95% |137, 184, 188, 354, 429-431, 435, 437, 440 |
| src/kraft/api/routes/settings.py             |      512 |       29 |     94% |102-103, 145-146, 258-259, 268-269, 287-288, 366, 408-409, 566-569, 572, 615-616, 677-680, 683-684, 721-722, 784-785, 800-801, 833 |
| src/kraft/api/routes/storage.py              |       51 |        0 |    100% |           |
| src/kraft/api/routes/work\_items.py          |      515 |       44 |     91% |182, 503-506, 627-645, 729, 731-732, 743, 860-861, 866-874, 896-897, 1106, 1122, 1137, 1156-1157, 1263-1264, 1329-1330, 1335 |
| src/kraft/api/routes/workers.py              |      244 |       14 |     94% |79-80, 105-106, 143, 155, 182-183, 199-203, 234, 302 |
| src/kraft/api/startup.py                     |      289 |       11 |     96% |192, 295-296, 373-374, 379-380, 396-397, 440-441 |
| src/kraft/apply.py                           |      135 |        6 |     96% |53, 80-81, 148-149, 217 |
| src/kraft/archive.py                         |       35 |        6 |     83% |     60-65 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       52 |        5 |     90% |72, 188-191 |
| src/kraft/automated\_review.py               |       21 |        1 |     95% |        62 |
| src/kraft/builtins.py                        |      821 |       51 |     94% |122-123, 131, 223, 305, 415, 429, 463, 467, 471, 477, 548-549, 569-570, 787, 903-904, 1039, 1058, 1116-1117, 1217-1218, 1409, 1528, 1563-1565, 1583-1584, 1672, 1678-1681, 1737-1738, 1750-1751, 1783-1784, 1827-1828, 1841, 1861-1862, 1877-1878, 2325-2328 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       38 |        0 |    100% |           |
| src/kraft/caps.py                            |      296 |       14 |     95% |143, 362, 388, 399, 425, 522, 594, 632-634, 678-681 |
| src/kraft/carried.py                         |      132 |        6 |     95% |105, 108, 136-139 |
| src/kraft/cli/\_\_init\_\_.py                |       60 |       12 |     80% |103-109, 142-149, 156 |
| src/kraft/cli/addon.py                       |      615 |       33 |     95% |163-164, 179, 201, 216-217, 220, 253, 260, 262-263, 297, 307-315, 559, 564, 592, 675, 712-713, 715, 719, 743, 775, 778-779, 901-902, 929-930, 969-970, 975 |
| src/kraft/cli/admin.py                       |     1034 |       91 |     91% |98, 193-195, 213, 219-223, 232, 287, 323-324, 419-420, 428, 442, 557, 680, 707, 837-838, 930-931, 1049-1052, 1200-1201, 1313, 1386-1387, 1407, 1420-1421, 1423, 1433-1434, 1439, 1442, 1447-1450, 1454-1457, 1468-1469, 1484-1485, 1502-1503, 1512-1513, 1519-1523, 1548-1550, 1559, 1570-1575, 1587-1593, 1632-1633, 1666-1667, 1877, 1900-1902, 1921, 1932, 1946-1950, 1971, 1977, 2016 |
| src/kraft/cli/common.py                      |       27 |        0 |    100% |           |
| src/kraft/cli/item.py                        |      383 |        8 |     98% |32, 265, 278, 376, 443-444, 457, 516 |
| src/kraft/cli/repo.py                        |      378 |       17 |     96% |139, 147, 162, 175-176, 231, 233, 333, 364, 425, 429, 483, 492, 494, 499, 552-553 |
| src/kraft/cli/templates.py                   |      112 |        1 |     99% |       157 |
| src/kraft/cli/verify.py                      |      195 |        8 |     96% |134, 268-271, 298, 314, 321 |
| src/kraft/cli/view.py                        |      303 |       10 |     97% |82, 274-275, 297, 307, 357-358, 394-400 |
| src/kraft/cli/worker.py                      |      211 |       41 |     81% |89, 193, 196-219, 223-233, 237-248, 252-254, 259 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      257 |       33 |     87% |72, 189, 207, 250, 263, 336, 403, 428, 430, 432, 439-440, 469, 478-482, 560-561, 583, 626, 666, 683, 709, 774, 797, 802-805, 810, 816 |
| src/kraft/client/context.py                  |       66 |        3 |     95% | 59-60, 78 |
| src/kraft/client/reads.py                    |      203 |        4 |     98% |325, 329, 445, 492 |
| src/kraft/client/transport.py                |      131 |        8 |     94% |101, 116-117, 134, 249-250, 263-264 |
| src/kraft/config.py                          |      787 |       19 |     98% |111, 134, 191-193, 209-211, 256-257, 412, 741, 989, 1033-1034, 1076, 1252-1253, 1340 |
| src/kraft/config\_schemas.py                 |       84 |        7 |     92% |   204-210 |
| src/kraft/console/\_\_init\_\_.py            |        0 |        0 |    100% |           |
| src/kraft/console/acts.py                    |       92 |        1 |     99% |        38 |
| src/kraft/console/app.py                     |      462 |        4 |     99% |327-328, 384, 645 |
| src/kraft/console/chain.py                   |      187 |        4 |     98% |209, 220, 222-223 |
| src/kraft/console/dialogs.py                 |      212 |        2 |     99% |  230, 369 |
| src/kraft/console/live.py                    |      105 |        1 |     99% |       114 |
| src/kraft/console/moments.py                 |       78 |        0 |    100% |           |
| src/kraft/console/output.py                  |       53 |        0 |    100% |           |
| src/kraft/console/panels.py                  |       97 |        4 |     96% |59, 157-159 |
| src/kraft/console/prompt.py                  |      137 |        4 |     97% |57, 125, 144, 187 |
| src/kraft/console/pulse.py                   |      119 |        0 |    100% |           |
| src/kraft/console/runner.py                  |      113 |        0 |    100% |           |
| src/kraft/console/screens/\_\_init\_\_.py    |       19 |        1 |     95% |        28 |
| src/kraft/console/screens/addons.py          |      236 |        5 |     98% |298-301, 306, 395 |
| src/kraft/console/screens/board.py           |      392 |        9 |     98% |374, 377, 388, 515, 551-555 |
| src/kraft/console/screens/item.py            |      483 |       10 |     98% |451, 463-464, 487, 536, 626-627, 642, 701-702 |
| src/kraft/console/screens/item\_chain.py     |      137 |        4 |     97% |66-67, 169, 189 |
| src/kraft/console/screens/item\_menu.py      |       97 |        0 |    100% |           |
| src/kraft/console/screens/new\_item.py       |       74 |        1 |     99% |       141 |
| src/kraft/console/screens/repos.py           |       84 |        2 |     98% |   105-106 |
| src/kraft/console/screens/server.py          |      166 |        1 |     99% |       279 |
| src/kraft/console/status.py                  |       91 |        0 |    100% |           |
| src/kraft/console/tail.py                    |       49 |        1 |     98% |        68 |
| src/kraft/console/theme.py                   |        3 |        0 |    100% |           |
| src/kraft/console/words.py                   |      407 |        4 |     99% |109, 350, 473, 501 |
| src/kraft/db.py                              |      148 |        4 |     97% |1448, 1474-1476 |
| src/kraft/detect.py                          |     1085 |       64 |     94% |202, 357-359, 366-368, 455-462, 483, 578-579, 593, 601, 615, 635, 680, 686, 900, 913, 931, 949, 951, 1012-1013, 1087-1088, 1102, 1178, 1181, 1260, 1272, 1285, 1314-1316, 1326, 1467-1468, 1579, 1669-1670, 1672, 1845-1863, 1950, 2017 |
| src/kraft/doctor.py                          |      789 |       47 |     94% |104-105, 181, 344-345, 424-425, 479, 483, 501, 505-506, 548, 601, 605, 651-652, 670-671, 759-760, 803-810, 860-861, 940-941, 1083-1084, 1137, 1149-1150, 1306-1307, 1390, 1408, 1421, 1431, 1473, 1481, 1577-1578, 1606, 1610-1611, 1637-1638 |
| src/kraft/drafts/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/drafts/areas.py                    |        6 |        0 |    100% |           |
| src/kraft/drafts/authored.py                 |      250 |       15 |     94% |90, 142, 166, 263, 309, 322, 327, 335, 348-349, 369, 374, 380, 398, 403 |
| src/kraft/drafts/config.py                   |      114 |        9 |     92% |47, 102-103, 121-122, 173-174, 176-177 |
| src/kraft/drafts/extending.py                |      402 |       16 |     96% |62, 137, 160, 240, 252, 285, 290, 316, 361, 377, 386, 418, 422, 437, 481, 623 |
| src/kraft/drafts/harnesses.py                |      324 |       21 |     94% |141, 159, 166, 240, 243, 245, 256, 258, 266, 270-273, 276, 280, 347, 358, 371-372, 442-444 |
| src/kraft/drafts/intake.py                   |      117 |        4 |     97% |47, 110-111, 181 |
| src/kraft/drafts/item.py                     |       88 |        2 |     98% |   88, 118 |
| src/kraft/drafts/ops.py                      |      843 |       75 |     91% |94, 144, 183-184, 205, 214, 216, 221, 227, 233, 239-240, 246-247, 263, 268, 362, 369-370, 426, 463, 465, 467, 507, 509, 546-547, 577, 608, 628, 650, 655-656, 661, 671, 708, 755, 771-776, 788, 813, 843, 851, 857, 879, 905, 934, 938, 941, 968, 973, 1020, 1045, 1063, 1080, 1097, 1099, 1103-1105, 1108, 1111, 1126, 1129, 1156, 1162-1163, 1173, 1175, 1288-1289 |
| src/kraft/drafts/policy.py                   |      178 |       12 |     93% |58, 72, 89-91, 106, 174, 187-188, 248, 250, 253 |
| src/kraft/drafts/policy\_caps.py             |       60 |        3 |     95% | 43, 49-50 |
| src/kraft/drafts/preserve.py                 |      325 |       18 |     94% |101, 121, 124, 214-215, 250-251, 254, 383-390, 395, 505, 553 |
| src/kraft/drafts/repos.py                    |      162 |        8 |     95% |37, 45, 50, 78, 183, 226, 231-232 |
| src/kraft/drafts/resolve.py                  |      524 |       25 |     95% |123, 130-131, 187-188, 217, 223-224, 295-296, 391-392, 407-408, 447, 465, 632, 653, 808, 816-817, 832, 864, 896-897 |
| src/kraft/drafts/store.py                    |       78 |        0 |    100% |           |
| src/kraft/escalate.py                        |      231 |        5 |     98% |213, 319, 467, 650-661 |
| src/kraft/events.py                          |       31 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       63 |        2 |     97% |  269, 300 |
| src/kraft/executor/dispatch.py               |     1111 |       35 |     97% |143-144, 342, 399, 417, 428, 463, 562, 598-599, 749, 1029, 1042, 1448-1449, 1636-1637, 1702-1704, 1969-1970, 1982-1984, 2054, 2087-2090, 2468, 2476, 3219, 3576, 3582 |
| src/kraft/executor/entry.py                  |      204 |        1 |     99% |       484 |
| src/kraft/executor/fallback.py               |       83 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      397 |       10 |     97% |250, 377, 380, 383, 388, 878, 895, 1009-1010, 1409 |
| src/kraft/executor/prompts.py                |      153 |        6 |     96% |159-166, 285 |
| src/kraft/executor/read\_only.py             |       82 |        2 |     98% |   79, 100 |
| src/kraft/executor/resuming.py               |       95 |        2 |     98% |  160, 203 |
| src/kraft/executor/retry.py                  |       26 |        0 |    100% |           |
| src/kraft/executor/round\_prompts.py         |      135 |        5 |     96% |275, 303, 324-325, 340 |
| src/kraft/executor/stops.py                  |      154 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |      970 |       30 |     97% |101-102, 733, 802-803, 891, 991, 1004, 1050, 1229-1230, 1273-1274, 1277, 1390-1395, 1834, 1880, 1894, 1970-1985, 1987, 2047, 2318, 2500, 2740, 2797, 2853 |
| src/kraft/findings.py                        |      112 |        0 |    100% |           |
| src/kraft/gate\_review.py                    |      100 |        4 |     96% |104-107, 209, 388 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      335 |       18 |     95% |194, 202, 217, 238, 258, 265, 272, 284, 404, 563, 565, 567, 576, 595-596, 633-635 |
| src/kraft/ids.py                             |       29 |        0 |    100% |           |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |158-159, 168-170 |
| src/kraft/index/embed.py                     |       75 |        9 |     88% |42-43, 80, 100-102, 122-124 |
| src/kraft/index/ingest.py                    |      262 |        9 |     97% |236-237, 358, 448-450, 560-562 |
| src/kraft/index/service.py                   |      470 |       31 |     93% |141-143, 159, 164, 219, 291-293, 330, 349-351, 404-405, 410, 413-414, 642, 693-694, 710, 728, 732-733, 928, 931, 937, 960, 963, 1021 |
| src/kraft/init.py                            |       51 |        2 |     96% |     62-64 |
| src/kraft/inplace/\_\_init\_\_.py            |       37 |        0 |    100% |           |
| src/kraft/inplace/base.py                    |      127 |        5 |     96% |96-98, 252, 267 |
| src/kraft/inplace/rebasing.py                |      122 |        9 |     93% |71, 103, 110-111, 118, 138-139, 239-240 |
| src/kraft/inplace/taking.py                  |      146 |        4 |     97% |43, 50, 64, 221 |
| src/kraft/inplace/task\_check.py             |      207 |        3 |     99% |188, 354-355 |
| src/kraft/intake.py                          |      144 |       13 |     91% |58, 102-104, 120, 163-164, 202-204, 245-250, 287 |
| src/kraft/intent.py                          |      198 |        9 |     95% |91-92, 186, 203, 264-265, 321-322, 339 |
| src/kraft/item\_paths.py                     |      307 |       59 |     81% |137, 397, 425, 443-444, 460-463, 475-491, 501-505, 508-510, 527-542, 549-550, 563, 574-577, 648-649, 671 |
| src/kraft/logs.py                            |      192 |       10 |     95% |105, 110, 127, 137, 154, 170-171, 179-180, 280 |
| src/kraft/mcp.py                             |      173 |        2 |     99% |   717-718 |
| src/kraft/mr\_poller.py                      |       68 |        9 |     87% |72-73, 85-86, 92, 127-130 |
| src/kraft/node\_runs.py                      |      104 |        5 |     95% |51, 116-117, 154-155 |
| src/kraft/notify.py                          |      153 |       10 |     93% |184-185, 269-276, 289-292, 318 |
| src/kraft/overrides.py                       |      120 |        4 |     97% |140, 144, 218-219 |
| src/kraft/paths.py                           |      108 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       70 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/pidfile.py                         |      125 |       19 |     85% |51-52, 57-58, 80-81, 117, 134, 145-146, 160-161, 169-170, 172, 196-197, 199-200 |
| src/kraft/policy.py                          |      828 |       24 |     97% |174, 297-298, 608, 613-614, 1329, 1521, 1755-1764, 1844, 1851, 1865, 1869-1873, 1878 |
| src/kraft/proc\_identity.py                  |       44 |        6 |     86% |24-25, 33-34, 96-97 |
| src/kraft/progress.py                        |      162 |        0 |    100% |           |
| src/kraft/rate\_limit\_retry.py              |       71 |        8 |     89% |56, 167-169, 190-193 |
| src/kraft/registration.py                    |       59 |        0 |    100% |           |
| src/kraft/remote/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/remote/blobs.py                    |       56 |        0 |    100% |           |
| src/kraft/remote/bundle.py                   |       67 |        7 |     90% |58, 83, 94-95, 116, 131-132 |
| src/kraft/remote/channels.py                 |       65 |       13 |     80% |62-63, 68-71, 74-75, 93-97 |
| src/kraft/remote/check.py                    |       66 |        6 |     91% |106-107, 110-111, 115, 118 |
| src/kraft/remote/daemon.py                   |      151 |        6 |     96% |46-47, 82, 201-202, 204 |
| src/kraft/remote/files.py                    |      141 |        4 |     97% |56, 111, 162-163 |
| src/kraft/remote/forwarder.py                |       41 |        2 |     95% |     61-62 |
| src/kraft/remote/hub.py                      |      321 |       13 |     96% |201-203, 206-207, 283, 318, 391, 413, 420, 496, 525-526 |
| src/kraft/remote/jobs.py                     |      260 |       15 |     94% |168-169, 196-199, 222, 242, 266-267, 319-320, 348-350 |
| src/kraft/remote/launch.py                   |      205 |       16 |     92% |94-95, 110-112, 139-150, 161-162, 197, 200-202, 254, 279, 397 |
| src/kraft/remote/lfs.py                      |      141 |        6 |     96% |84, 105, 119, 212-213, 251 |
| src/kraft/remote/listener.py                 |       76 |        0 |    100% |           |
| src/kraft/remote/local\_jobs.py              |      145 |       17 |     88% |59, 73, 77, 120-121, 135-136, 141-142, 158-160, 186-189, 205 |
| src/kraft/remote/local\_link.py              |      261 |        7 |     97% |89, 299-301, 327, 347, 395 |
| src/kraft/remote/local\_serve.py             |      130 |       13 |     90% |42-43, 69-76, 97-98, 158 |
| src/kraft/remote/machine.py                  |      430 |       44 |     90% |52, 127-130, 144-153, 240, 243, 274-276, 301, 465, 486-494, 497-498, 508-510, 553-554, 658, 664-669, 682-684, 691-696, 717 |
| src/kraft/remote/machine\_config.py          |       23 |        0 |    100% |           |
| src/kraft/remote/paths.py                    |       19 |        0 |    100% |           |
| src/kraft/remote/placement.py                |       85 |        1 |     99% |       120 |
| src/kraft/remote/protocol.py                 |      118 |        4 |     97% |200, 259-260, 268 |
| src/kraft/remote/registry.py                 |      155 |        0 |    100% |           |
| src/kraft/remote/respawn.py                  |       16 |        1 |     94% |        20 |
| src/kraft/remote/runs.py                     |      387 |       28 |     93% |103-104, 142-143, 277-278, 316, 452-453, 494, 509-510, 514-515, 543-563, 574-575 |
| src/kraft/remote/sandboxed.py                |      145 |       19 |     87% |92, 97, 113-114, 225, 242-244, 261, 274-279, 286, 327-332 |
| src/kraft/remote/sockets.py                  |       27 |        2 |     93% |    31, 33 |
| src/kraft/remote/standin.py                  |       72 |       55 |     24% |44-45, 57-125 |
| src/kraft/remote/transport.py                |       57 |        5 |     91% |36-37, 45, 72-73 |
| src/kraft/remote/workdir.py                  |       86 |        0 |    100% |           |
| src/kraft/render.py                          |      286 |       24 |     92% |59-60, 105, 112, 396, 407, 422, 435, 441-445, 496-503, 554, 566, 592 |
| src/kraft/review.py                          |      183 |        5 |     97% |99, 227, 231, 279, 332 |
| src/kraft/review\_reply.py                   |       81 |        6 |     93% |97-98, 130-131, 136, 158 |
| src/kraft/reviewed\_head.py                  |      102 |        2 |     98% |   228-229 |
| src/kraft/skill.py                           |       88 |        8 |     91% |83-84, 113, 199, 204-205, 209-210 |
| src/kraft/start\_check.py                    |       39 |        6 |     85% |40-42, 49-50, 58 |
| src/kraft/start\_queue.py                    |      126 |        5 |     96% |124, 267-270 |
| src/kraft/storage.py                         |      276 |       14 |     95% |102-103, 132-133, 168-169, 185-186, 205, 365, 487-490 |
| src/kraft/store/\_\_init\_\_.py              |       13 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       51 |        2 |     96% |  137, 155 |
| src/kraft/store/budget.py                    |       69 |        0 |    100% |           |
| src/kraft/store/chain.py                     |      208 |        4 |     98% |412, 502-504 |
| src/kraft/store/counters.py                  |       55 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       89 |        0 |    100% |           |
| src/kraft/store/intake.py                    |       14 |        0 |    100% |           |
| src/kraft/store/passes.py                    |       62 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      181 |        4 |     98% |212, 217, 224-225 |
| src/kraft/store/sessions.py                  |      214 |        0 |    100% |           |
| src/kraft/store/work\_items.py               |      369 |        5 |     99% |415, 461, 570, 766, 773 |
| src/kraft/templates/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py             |       86 |        0 |    100% |           |
| src/kraft/templates/environment.py           |      343 |        6 |     98% |546, 548, 614-615, 639-640 |
| src/kraft/templates/extension.py             |      216 |       10 |     95% |68-69, 133, 210, 230, 236, 248, 281, 291-292 |
| src/kraft/templates/forks.py                 |      100 |        0 |    100% |           |
| src/kraft/templates/library.py               |      928 |       24 |     97% |154, 275-276, 324, 331, 520-521, 540, 562-563, 576-577, 627, 636-637, 774, 1234, 1300-1301, 1494, 1519-1521, 1591, 1716 |
| src/kraft/templates/models.py                |      835 |        7 |     99% |125, 128, 651, 859, 1647-1648, 1952 |
| src/kraft/templates/positions.py             |       57 |        0 |    100% |           |
| src/kraft/templates/retry.py                 |      105 |        7 |     93% |193, 203-205, 208-209, 216 |
| src/kraft/templates/revision.py              |      273 |        9 |     97% |219, 264, 318, 445, 531, 577-578, 580, 594 |
| src/kraft/triggers.py                        |       79 |        6 |     92% |107-108, 148-151 |
| src/kraft/update.py                          |      315 |       20 |     94% |80-81, 111-115, 120, 203-204, 233-236, 342-343, 345, 446-447, 554, 574, 586 |
| src/kraft/usage.py                           |      485 |       22 |     95% |119-120, 226, 357, 478, 538, 558-559, 562-563, 607, 686, 706-707, 978-979, 1084-1085, 1118-1119, 1269, 1281 |
| src/kraft/vocab/\_\_init\_\_.py              |        7 |        0 |    100% |           |
| src/kraft/vocab/display.py                   |       14 |        0 |    100% |           |
| src/kraft/vocab/events/\_\_init\_\_.py       |       33 |        0 |    100% |           |
| src/kraft/vocab/events/chain.py              |       34 |        0 |    100% |           |
| src/kraft/vocab/events/escalation.py         |       15 |        0 |    100% |           |
| src/kraft/vocab/events/forge.py              |       28 |        0 |    100% |           |
| src/kraft/vocab/events/gate.py               |       26 |        0 |    100% |           |
| src/kraft/vocab/events/limit.py              |       17 |        0 |    100% |           |
| src/kraft/vocab/events/remote.py             |       13 |        0 |    100% |           |
| src/kraft/vocab/events/sandbox.py            |       14 |        0 |    100% |           |
| src/kraft/vocab/events/session.py            |       17 |        0 |    100% |           |
| src/kraft/vocab/events/settings.py           |       14 |        0 |    100% |           |
| src/kraft/vocab/events/traits.py             |        8 |        0 |    100% |           |
| src/kraft/vocab/events/work\_item.py         |       35 |        0 |    100% |           |
| src/kraft/vocab/review.py                    |       22 |        0 |    100% |           |
| src/kraft/vocab/session.py                   |       43 |        0 |    100% |           |
| src/kraft/vocab/sql.py                       |       29 |        0 |    100% |           |
| src/kraft/vocab/stop.py                      |       27 |        0 |    100% |           |
| src/kraft/vocab/total.py                     |       11 |        0 |    100% |           |
| src/kraft/vocab/work\_item.py                |       98 |        0 |    100% |           |
| src/kraft/waits.py                           |      113 |        8 |     93% |260, 333-335, 345-348 |
| src/kraft/when\_free.py                      |      426 |       33 |     92% |76, 155-157, 196-198, 247, 393, 395, 442-444, 560-562, 578-580, 594, 609-615, 623-636, 644, 805 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       36 |        0 |    100% |           |
| src/kraft/worker/backends/bwrap.py           |      325 |       26 |     92% |171, 212, 257-260, 275-276, 292, 314, 327, 379, 382, 386, 403, 417, 442, 526-527, 531, 563, 595, 622, 625-627 |
| src/kraft/worker/backends/docker.py          |      741 |       53 |     93% |326, 338-341, 353-360, 437, 552, 600, 609-610, 628, 658, 683-684, 711, 714-717, 786, 1000-1001, 1111-1112, 1139, 1199, 1291-1294, 1387-1388, 1413-1414, 1457, 1521-1522, 1566, 1594, 1663-1664, 1703-1704, 1797-1798, 1826-1827 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/backends/seatbelt.py        |      150 |        3 |     98% |   365-367 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       306 |
| src/kraft/worker/callback.py                 |       37 |        1 |     97% |        43 |
| src/kraft/worker/channel.py                  |      174 |        7 |     96% |179, 187, 221-222, 323, 349-350 |
| src/kraft/worker/egress.py                   |      485 |       36 |     93% |292-293, 299, 340-342, 361, 398, 404, 420-421, 431, 449, 451, 511, 566, 574-575, 710, 721, 744, 760, 784-785, 787-788, 800, 803, 833-834, 884-889 |
| src/kraft/worker/env.py                      |       11 |        0 |    100% |           |
| src/kraft/worker/host\_view.py               |      202 |       11 |     95% |157, 159-160, 166, 179, 196-197, 202, 206, 321, 333 |
| src/kraft/worker/inject.py                   |      238 |       18 |     92% |194, 214, 230, 241, 253, 351, 365, 374, 384, 390, 393, 407, 410, 415, 424-427 |
| src/kraft/worker/kit.py                      |      421 |       10 |     98% |124, 238-240, 430, 669-670, 672, 698, 732 |
| src/kraft/worker/mounts.py                   |      102 |        2 |     98% |   230-231 |
| src/kraft/worker/os\_init.py                 |      395 |      204 |     48% |75, 85, 89-100, 104-105, 109-183, 190-205, 224, 231-258, 269-270, 290-291, 322-323, 343, 349-351, 363, 391, 446-450, 474-475, 480-543, 547-551, 555 |
| src/kraft/worker/os\_seccomp.py              |       34 |        0 |    100% |           |
| src/kraft/worker/reattach.py                 |      377 |       27 |     93% |118-119, 128-129, 212-213, 326, 372, 384-385, 416, 489, 506-507, 515, 531-532, 601, 752-753, 796, 854-858, 871-872, 889-890 |
| src/kraft/worker/refstore.py                 |      325 |        9 |     97% |210, 213, 222, 426-427, 589-590, 620, 643 |
| src/kraft/worker/sandbox.py                  |      176 |        5 |     97% |119, 163-164, 201, 226 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |45, 50-51, 53 |
| src/kraft/worker/shim.py                     |       10 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       37 |        0 |    100% |           |
| src/kraft/worker/tree\_walk.py               |       99 |       12 |     88% |71-72, 132-133, 135-136, 171-172, 181-182, 185-186 |
| src/kraft/worker/worktree\_read.py           |       90 |        8 |     91% |52, 165-167, 170-174, 182-183 |
| src/kraft/ws.py                              |       72 |        3 |     96% |     77-79 |
| **TOTAL**                                    | **54604** | **2743** | **95%** |           |


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