# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/itsOmidKarami/kraft/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | ------: | --------: |
| src/kraft/\_\_init\_\_.py                    |        0 |        0 |    100% |           |
| src/kraft/\_\_main\_\_.py                    |        4 |        4 |      0% |       1-6 |
| src/kraft/adapters/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| src/kraft/adapters/agent.py                  |      440 |       21 |     95% |460, 559-560, 562, 836, 907-910, 986-989, 1173, 1355-1356, 1388-1394 |
| src/kraft/adapters/artifact\_notes.py        |        2 |        0 |    100% |           |
| src/kraft/adapters/beads.py                  |       64 |       20 |     69% |61, 94-100, 134, 137-138, 194-202 |
| src/kraft/adapters/forge/\_\_init\_\_.py     |       10 |        0 |    100% |           |
| src/kraft/adapters/forge/api\_account.py     |       82 |        2 |     98% |    46, 49 |
| src/kraft/adapters/forge/ci.py               |       45 |        0 |    100% |           |
| src/kraft/adapters/forge/credentials.py      |      316 |       19 |     94% |98-99, 144-145, 157-158, 175-176, 178, 237, 259, 284, 293, 412, 437-439, 458-459 |
| src/kraft/adapters/forge/gh.py               |      461 |       22 |     95% |607, 634-635, 711-713, 822, 845, 864, 891-892, 905-906, 957, 1030-1031, 1064, 1112, 1138-1139, 1148-1149 |
| src/kraft/adapters/forge/git.py              |      291 |       14 |     95% |259-260, 277-278, 301-302, 335-336, 400-402, 597-604, 1003 |
| src/kraft/adapters/forge/git\_auth.py        |      183 |        1 |     99% |       276 |
| src/kraft/adapters/forge/github\_api.py      |       27 |        0 |    100% |           |
| src/kraft/adapters/forge/github\_queries.py  |      108 |        6 |     94% |100, 115, 127, 144-145, 151 |
| src/kraft/adapters/forge/gitlab\_api.py      |       11 |        0 |    100% |           |
| src/kraft/adapters/forge/glab.py             |      544 |       25 |     95% |354, 453-454, 537, 575-576, 674, 687, 732-733, 819, 895, 983-984, 1029, 1039-1040, 1042, 1082, 1099, 1166-1167, 1297, 1340-1341 |
| src/kraft/adapters/forge/http.py             |      187 |        3 |     98% |79-80, 344 |
| src/kraft/adapters/forge/models.py           |      392 |        1 |     99% |       949 |
| src/kraft/adapters/forge/mr.py               |      162 |       12 |     93% |89, 155-163, 328-331, 397, 405 |
| src/kraft/adapters/forge/probe.py            |      189 |        7 |     96% |71, 73, 112, 193, 206, 305-306 |
| src/kraft/adapters/forge/run.py              |     1027 |       43 |     96% |206-207, 410, 425-426, 553-557, 613, 697-699, 717, 860-864, 1155, 1514, 1717-1718, 1779-1781, 1811, 1915, 1934-1935, 1947, 2159-2160, 2195-2206, 2903, 3013-3019, 3039 |
| src/kraft/adapters/forge/settle.py           |       73 |        0 |    100% |           |
| src/kraft/adapters/forge/transport.py        |      133 |        4 |     97% |136-137, 206, 256 |
| src/kraft/adapters/hook\_install.py          |      232 |       10 |     96% |91, 186-189, 265-266, 291-292, 384 |
| src/kraft/adapters/profiles.py               |       35 |        0 |    100% |           |
| src/kraft/adapters/subprocess.py             |      667 |       18 |     97% |238, 412-413, 697, 772, 813-814, 892-893, 1076-1077, 1529, 1531, 1542, 1666-1667, 1711-1712 |
| src/kraft/addons/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/addons/builtin.py                  |      195 |        2 |     99% |   40, 197 |
| src/kraft/addons/config.py                   |      153 |        3 |     98% |152, 154, 166 |
| src/kraft/addons/drift.py                    |       89 |        6 |     93% |93-94, 109-110, 141-142 |
| src/kraft/addons/fetch.py                    |      326 |       16 |     95% |100-101, 196, 222-223, 265, 269, 409, 433, 525-526, 531-536 |
| src/kraft/addons/flow.py                     |      105 |        5 |     95% |96, 98, 100, 136-137 |
| src/kraft/addons/inherit.py                  |      350 |        7 |     98% |69-70, 346, 433, 513, 520, 635 |
| src/kraft/addons/load.py                     |      514 |       34 |     93% |109, 204, 272-273, 519-520, 603-604, 796-797, 826-827, 849-850, 871, 878-879, 939-940, 964, 969, 991-992, 994, 997-998, 1000, 1007-1008, 1024, 1051-1052, 1081-1082 |
| src/kraft/addons/manifest.py                 |      206 |        9 |     96% |122, 142, 148-150, 221, 258, 311-312 |
| src/kraft/addons/repo\_library.py            |      378 |       22 |     94% |88-89, 109, 115, 131-132, 225-226, 299-300, 314, 327, 345, 409-410, 429-430, 604-605, 655-656, 660 |
| src/kraft/addons/review.py                   |      298 |        9 |     97% |146-147, 194-195, 258, 387-390 |
| src/kraft/addons/update.py                   |      921 |       31 |     97% |107, 311-312, 330-331, 359-360, 383-384, 579, 618-619, 657-658, 797, 860-861, 1027-1028, 1096, 1102, 1290-1292, 1371-1372, 1582, 1599, 1608, 1698, 1836 |
| src/kraft/analytics.py                       |      283 |        7 |     98% |69-70, 191, 197, 504-506 |
| src/kraft/answers.py                         |      131 |        3 |     98% |206, 209, 290 |
| src/kraft/api/\_\_init\_\_.py                |       53 |        0 |    100% |           |
| src/kraft/api/apidocs.py                     |       24 |        0 |    100% |           |
| src/kraft/api/config\_check.py               |      503 |       46 |     91% |135, 212, 227-228, 302, 338-339, 349-350, 376, 379-380, 455-456, 458, 475-476, 503, 542, 617-620, 645, 786-787, 804-805, 807, 809-814, 832-833, 848-849, 960-961, 980, 1002-1005, 1008-1009 |
| src/kraft/api/deps.py                        |      619 |       30 |     95% |104-105, 202, 273-274, 614, 806, 809-811, 943, 959, 961, 1006-1007, 1035-1036, 1078-1079, 1088-1089, 1098-1099, 1238-1239, 1274, 1277, 1280, 1308-1309 |
| src/kraft/api/item\_ids.py                   |       26 |        0 |    100% |           |
| src/kraft/api/perimeter.py                   |      133 |        2 |     98% |  271, 426 |
| src/kraft/api/routes/\_\_init\_\_.py         |        0 |        0 |    100% |           |
| src/kraft/api/routes/admin.py                |       88 |        1 |     99% |       138 |
| src/kraft/api/routes/artifacts.py            |      309 |       13 |     96% |165, 223-224, 345-352, 399-400, 461, 490, 662 |
| src/kraft/api/routes/auth.py                 |       74 |        0 |    100% |           |
| src/kraft/api/routes/board.py                |      545 |        4 |     99% |694, 1083, 1182, 1185 |
| src/kraft/api/routes/check.py                |       20 |        0 |    100% |           |
| src/kraft/api/routes/drafts.py               |      311 |        9 |     97% |273, 289, 347-348, 354, 370, 387, 441, 557 |
| src/kraft/api/routes/forge\_reads.py         |       74 |        0 |    100% |           |
| src/kraft/api/routes/forges.py               |      252 |        5 |     98% |98-99, 194, 308, 313 |
| src/kraft/api/routes/gates.py                |      194 |        7 |     96% |134, 137, 156, 432-433, 517-518 |
| src/kraft/api/routes/harnesses.py            |       98 |        1 |     99% |       264 |
| src/kraft/api/routes/kraft\_default.py       |        7 |        0 |    100% |           |
| src/kraft/api/routes/lifecycle.py            |     1281 |       67 |     95% |257, 260-261, 266-267, 327, 343, 436, 449, 533-536, 557-558, 579, 733, 782-783, 834, 1021, 1088, 1103, 1135-1146, 1182-1183, 1278, 1298, 1418-1419, 1429, 1613-1614, 1663, 1745, 1747, 1792, 1930, 1962, 1971, 2051, 2159-2160, 2233, 2409, 2414-2415, 2431, 2531, 2607-2608, 2618, 2706-2707, 2757-2758, 2760, 2871, 3008-3009, 3066, 3110, 3133-3135, 3145 |
| src/kraft/api/routes/one\_off.py             |       48 |        0 |    100% |           |
| src/kraft/api/routes/one\_off\_editor.py     |       86 |        1 |     99% |       119 |
| src/kraft/api/routes/repos.py                |      427 |       21 |     95% |114-115, 142-143, 283, 353-354, 365, 431-432, 462, 479-480, 558-559, 734-735, 894-895, 920-921 |
| src/kraft/api/routes/review.py               |      357 |       14 |     96% |86, 90, 101, 164, 191, 223, 263, 265, 276, 340, 526, 571-572, 669 |
| src/kraft/api/routes/search.py               |      158 |       11 |     93% |50-51, 85-88, 91, 138, 151-152, 191-192, 253 |
| src/kraft/api/routes/sessions.py             |      214 |       10 |     95% |171, 220, 224, 390, 465-467, 471, 473, 476 |
| src/kraft/api/routes/settings.py             |      502 |       29 |     94% |103-104, 147-148, 260-261, 270-271, 289-290, 370, 398-399, 563-566, 569, 613-614, 677-680, 683-684, 721-722, 784-785, 800-801, 835 |
| src/kraft/api/routes/storage.py              |       51 |        0 |    100% |           |
| src/kraft/api/routes/work\_items.py          |      603 |       39 |     94% |201, 350-351, 615-618, 741-759, 852, 854-855, 866, 999, 1021-1022, 1248, 1264, 1283, 1302-1303, 1429-1430, 1506-1507, 1512 |
| src/kraft/api/routes/workers.py              |      244 |       14 |     94% |79-80, 105-106, 143, 155, 182-183, 199-203, 236, 305 |
| src/kraft/api/startup.py                     |      325 |       13 |     96% |221, 326-327, 404-405, 410-411, 433-434, 442-443, 486-487 |
| src/kraft/apply.py                           |      176 |        8 |     95% |73, 100-101, 168-169, 248-249, 287 |
| src/kraft/archive.py                         |       61 |        7 |     89% |61-63, 112-115 |
| src/kraft/auth.py                            |       75 |        8 |     89% |44, 48, 50-51, 96-99 |
| src/kraft/auto\_escalate\_delay.py           |       52 |        5 |     90% |72, 188-191 |
| src/kraft/automated\_review.py               |       21 |        1 |     95% |        62 |
| src/kraft/builtins.py                        |      862 |       50 |     94% |135-136, 144, 236, 318, 441, 455, 520, 537, 545, 616-617, 637-638, 893, 1016-1017, 1152, 1171, 1229-1230, 1340-1341, 1551, 1670, 1705-1707, 1725-1726, 1814, 1820-1823, 1879-1880, 1892-1893, 1925-1926, 1969-1970, 1983, 2003-2004, 2019-2020, 2478-2481 |
| src/kraft/cap\_levels.py                     |       59 |        0 |    100% |           |
| src/kraft/capabilities.py                    |       40 |        3 |     92% | 79-80, 83 |
| src/kraft/caps.py                            |      335 |       17 |     95% |143, 362, 388, 399, 425, 522, 601, 641-643, 694, 697, 755, 768-771 |
| src/kraft/carried.py                         |      132 |        6 |     95% |105, 108, 136-139 |
| src/kraft/ci\_reruns.py                      |       26 |        0 |    100% |           |
| src/kraft/cli/\_\_init\_\_.py                |       62 |       12 |     81% |110-116, 149-156, 163 |
| src/kraft/cli/addon.py                       |      639 |       33 |     95% |170-171, 186, 208, 223-224, 227, 260, 267, 269-270, 304, 317-325, 582, 587, 615, 700, 737-738, 740, 744, 768, 800, 803-804, 934-935, 960-961, 1002-1003, 1008 |
| src/kraft/cli/admin.py                       |     1111 |       94 |     92% |99, 219, 231-232, 242-246, 262, 317, 353-354, 449-450, 458, 472, 587, 720, 747, 877-878, 970-971, 1023-1025, 1044-1045, 1142-1145, 1293-1294, 1406, 1479-1480, 1500, 1513-1514, 1516, 1526-1527, 1532, 1535, 1540-1543, 1547-1550, 1561-1562, 1577-1578, 1595-1596, 1605-1606, 1612-1616, 1643-1645, 1657, 1668-1673, 1685-1691, 1730-1731, 1764-1765, 1975, 1998-2000, 2058, 2072-2076, 2097, 2103, 2152 |
| src/kraft/cli/common.py                      |       27 |        0 |    100% |           |
| src/kraft/cli/forge.py                       |      220 |       15 |     93% |69, 82, 109, 111, 118, 131-132, 158, 198, 222, 272, 274-276, 290 |
| src/kraft/cli/item.py                        |      407 |        9 |     98% |32, 282, 295, 393, 397, 479-480, 493, 552 |
| src/kraft/cli/repo.py                        |      574 |       30 |     95% |143, 151, 176, 189-190, 215, 247, 249, 349, 384, 445, 512, 514, 519, 541, 552, 589, 650, 671, 685, 725-726, 741, 749, 807-813, 879-880 |
| src/kraft/cli/templates.py                   |      112 |        1 |     99% |       157 |
| src/kraft/cli/verify.py                      |      195 |        8 |     96% |134, 268-271, 298, 314, 321 |
| src/kraft/cli/view.py                        |      368 |       11 |     97% |120, 186, 355-356, 378, 388, 498-499, 535-541 |
| src/kraft/cli/worker.py                      |      211 |       41 |     81% |89, 193, 196-219, 223-233, 237-248, 252-254, 259 |
| src/kraft/client/\_\_init\_\_.py             |        5 |        0 |    100% |           |
| src/kraft/client/actions.py                  |      284 |       36 |     87% |77, 132, 170, 209, 227, 266, 279, 352, 419, 444, 446, 448, 455-456, 485, 494-498, 577-578, 626, 669, 709, 726, 752, 817, 840, 845-848, 853, 859, 970 |
| src/kraft/client/context.py                  |       72 |        3 |     96% | 65-66, 84 |
| src/kraft/client/reads.py                    |      255 |       13 |     95% |369, 373, 509, 564, 729-730, 735, 740, 747, 758-759, 766, 771 |
| src/kraft/client/remote.py                   |      110 |        7 |     94% |126-128, 175-181 |
| src/kraft/client/transport.py                |      157 |        6 |     96% |162, 195, 310-311, 324-325 |
| src/kraft/config.py                          |      807 |       19 |     98% |112, 135, 192-194, 210-212, 262-263, 420, 775, 1023, 1067-1068, 1110, 1286-1287, 1374 |
| src/kraft/config\_schemas.py                 |      100 |        7 |     93% |   227-233 |
| src/kraft/console/\_\_init\_\_.py            |        0 |        0 |    100% |           |
| src/kraft/console/acts.py                    |       92 |        1 |     99% |        38 |
| src/kraft/console/app.py                     |      483 |        4 |     99% |348-349, 410, 683 |
| src/kraft/console/chain.py                   |      201 |        3 |     99% |213, 226-227 |
| src/kraft/console/dialogs.py                 |      212 |        2 |     99% |  230, 369 |
| src/kraft/console/live.py                    |      105 |        1 |     99% |       114 |
| src/kraft/console/moments.py                 |       78 |        0 |    100% |           |
| src/kraft/console/output.py                  |       53 |        0 |    100% |           |
| src/kraft/console/panels.py                  |       97 |        4 |     96% |59, 157-159 |
| src/kraft/console/prompt.py                  |      140 |        4 |     97% |56, 130, 149, 192 |
| src/kraft/console/pulse.py                   |      128 |        0 |    100% |           |
| src/kraft/console/runner.py                  |      113 |        0 |    100% |           |
| src/kraft/console/screens/\_\_init\_\_.py    |       19 |        1 |     95% |        28 |
| src/kraft/console/screens/addons.py          |      245 |        5 |     98% |309-312, 317, 406 |
| src/kraft/console/screens/board.py           |      405 |        9 |     98% |397, 400, 411, 540, 576-580 |
| src/kraft/console/screens/item.py            |      483 |       10 |     98% |451, 463-464, 487, 536, 626-627, 642, 701-702 |
| src/kraft/console/screens/item\_chain.py     |      138 |        4 |     97% |68-69, 172, 192 |
| src/kraft/console/screens/item\_menu.py      |      116 |        5 |     96% |136, 144-147 |
| src/kraft/console/screens/new\_item.py       |      103 |        4 |     96% |143-145, 178 |
| src/kraft/console/screens/repos.py           |       86 |        2 |     98% |   105-106 |
| src/kraft/console/screens/server.py          |      188 |        2 |     99% |  292, 324 |
| src/kraft/console/signin.py                  |       21 |        2 |     90% |    17, 21 |
| src/kraft/console/status.py                  |       91 |        0 |    100% |           |
| src/kraft/console/tail.py                    |       49 |        1 |     98% |        68 |
| src/kraft/console/theme.py                   |        3 |        0 |    100% |           |
| src/kraft/console/words.py                   |      440 |        5 |     99% |110, 397, 435, 554, 582 |
| src/kraft/db.py                              |      151 |        4 |     97% |1515, 1541-1543 |
| src/kraft/detect.py                          |     1085 |       63 |     94% |202, 357-359, 366-368, 455-462, 483, 578-579, 593, 601, 615, 635, 680, 686, 900, 913, 931, 949, 951, 1012-1013, 1087-1088, 1102, 1178, 1181, 1260, 1272, 1285, 1314-1316, 1326, 1467-1468, 1579, 1669-1670, 1672, 1845-1863, 1950 |
| src/kraft/doctor.py                          |      876 |       43 |     95% |106-107, 200, 364-365, 445-446, 510, 514, 587, 591, 637-638, 656-657, 745-746, 789-796, 846-847, 1116-1117, 1170, 1182-1183, 1339-1340, 1411-1412, 1506, 1551, 1561, 1617-1618, 1633, 1729-1730, 1758, 1762-1763, 1789-1790 |
| src/kraft/drafts/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/drafts/areas.py                    |        6 |        0 |    100% |           |
| src/kraft/drafts/authored.py                 |      250 |       15 |     94% |92, 144, 168, 265, 311, 324, 329, 337, 350-351, 371, 376, 382, 400, 405 |
| src/kraft/drafts/config.py                   |      114 |        9 |     92% |47, 102-103, 121-122, 173-174, 176-177 |
| src/kraft/drafts/extending.py                |      402 |       15 |     96% |137, 160, 240, 252, 285, 290, 316, 361, 377, 386, 418, 422, 437, 481, 623 |
| src/kraft/drafts/harnesses.py                |      334 |       19 |     94% |150, 168, 179, 255, 258, 260, 271, 273, 281, 285-288, 291, 295, 363, 374, 458-460 |
| src/kraft/drafts/intake.py                   |      117 |        4 |     97% |47, 110-111, 181 |
| src/kraft/drafts/item.py                     |       89 |        2 |     98% |   88, 118 |
| src/kraft/drafts/ops.py                      |      852 |       73 |     91% |96, 189-190, 211, 220, 222, 227, 233, 239, 245-246, 252-253, 269, 274, 368, 375-376, 432, 469, 471, 473, 513, 515, 552-553, 583, 614, 634, 656, 661-662, 667, 677, 714, 761, 777-782, 794, 819, 852, 860, 866, 888, 914, 943, 947, 950, 977, 1029, 1054, 1072, 1098, 1115, 1117, 1121-1123, 1126, 1129, 1144, 1147, 1174, 1180-1181, 1191, 1193, 1306-1307 |
| src/kraft/drafts/policy.py                   |      178 |       12 |     93% |59, 73, 90-92, 107, 175, 188-189, 249, 251, 254 |
| src/kraft/drafts/policy\_caps.py             |       60 |        3 |     95% | 43, 49-50 |
| src/kraft/drafts/preserve.py                 |      325 |       18 |     94% |101, 121, 124, 214-215, 250-251, 254, 383-390, 395, 505, 553 |
| src/kraft/drafts/repos.py                    |      164 |        8 |     95% |38, 46, 51, 79, 184, 227, 232-233 |
| src/kraft/drafts/resolve.py                  |      526 |       26 |     95% |124, 131-132, 188-189, 218, 224-225, 296-297, 392-393, 408-409, 448, 466, 633, 650, 658, 813, 821-822, 837, 869, 901-902 |
| src/kraft/drafts/store.py                    |       78 |        0 |    100% |           |
| src/kraft/escalate.py                        |      231 |        5 |     98% |213, 319, 467, 650-661 |
| src/kraft/events.py                          |       32 |        0 |    100% |           |
| src/kraft/executor/\_\_init\_\_.py           |       10 |        0 |    100% |           |
| src/kraft/executor/context.py                |       68 |        2 |     97% |  285, 316 |
| src/kraft/executor/dispatch.py               |     1187 |       33 |     97% |148-149, 349, 408, 437, 455, 466, 501, 538, 600, 636-637, 787, 1501-1502, 1755-1757, 2051-2052, 2064-2066, 2136, 2169-2172, 2630, 2638, 3386, 3753, 3759 |
| src/kraft/executor/entry.py                  |      204 |        1 |     99% |       490 |
| src/kraft/executor/fallback.py               |       83 |        0 |    100% |           |
| src/kraft/executor/gates.py                  |      419 |       11 |     97% |256, 398, 401, 404, 409, 921, 935, 952, 1066-1067, 1468 |
| src/kraft/executor/join.py                   |      368 |       32 |     91% |97, 104, 142-143, 152, 162, 197, 209, 316, 318, 321-324, 430-432, 435-437, 452-459, 490, 496, 526-528, 623-626, 708-709 |
| src/kraft/executor/prompts.py                |      205 |        7 |     97% |218-225, 344, 658 |
| src/kraft/executor/read\_only.py             |       82 |        2 |     98% |   84, 105 |
| src/kraft/executor/resuming.py               |       95 |        2 |     98% |  160, 203 |
| src/kraft/executor/retry.py                  |       26 |        0 |    100% |           |
| src/kraft/executor/round\_prompts.py         |      138 |        5 |     96% |286, 314, 335-336, 351 |
| src/kraft/executor/slices.py                 |      223 |       19 |     91% |82, 96, 126, 131, 188, 295-296, 360-362, 372-374, 432-433, 453, 477-479, 488 |
| src/kraft/executor/stops.py                  |      170 |        0 |    100% |           |
| src/kraft/executor/walk.py                   |     1044 |       30 |     97% |104-105, 821, 890-891, 979, 1079, 1092, 1138, 1317-1318, 1361-1362, 1365, 1478-1483, 1926, 1972, 1991, 2067-2082, 2084, 2144, 2415, 2631, 2929, 2986, 3042 |
| src/kraft/findings.py                        |      112 |        0 |    100% |           |
| src/kraft/forges.py                          |      224 |        6 |     97% |30-31, 56-57, 221-222 |
| src/kraft/gate\_review.py                    |      185 |        6 |     97% |180-181, 215-218, 323, 557 |
| src/kraft/grants.py                          |       64 |        0 |    100% |           |
| src/kraft/harness.py                         |      338 |       18 |     95% |202, 210, 225, 246, 266, 273, 280, 292, 413, 573, 575, 577, 586, 605-606, 643-645 |
| src/kraft/ids.py                             |       29 |        0 |    100% |           |
| src/kraft/index/\_\_init\_\_.py              |        0 |        0 |    100% |           |
| src/kraft/index/chunk.py                     |       44 |        1 |     98% |        65 |
| src/kraft/index/db.py                        |       68 |        5 |     93% |158-159, 168-170 |
| src/kraft/index/embed.py                     |       75 |        9 |     88% |42-43, 80, 100-102, 122-124 |
| src/kraft/index/ingest.py                    |      282 |        9 |     97% |242-243, 397, 487-489, 599-601 |
| src/kraft/index/service.py                   |      470 |       31 |     93% |141-143, 159, 164, 219, 291-293, 330, 349-351, 404-405, 410, 413-414, 642, 693-694, 710, 728, 732-733, 928, 931, 937, 960, 963, 1021 |
| src/kraft/init.py                            |       53 |        2 |     96% |     62-64 |
| src/kraft/inplace/\_\_init\_\_.py            |       44 |        0 |    100% |           |
| src/kraft/inplace/base.py                    |      127 |        5 |     96% |96-98, 252, 267 |
| src/kraft/inplace/copy.py                    |       74 |        0 |    100% |           |
| src/kraft/inplace/rebasing.py                |      122 |        9 |     93% |71, 103, 110-111, 118, 138-139, 239-240 |
| src/kraft/inplace/taking.py                  |      146 |        4 |     97% |43, 50, 64, 221 |
| src/kraft/inplace/task\_check.py             |      207 |        3 |     99% |188, 354-355 |
| src/kraft/intake.py                          |      144 |       13 |     91% |58, 102-104, 120, 163-164, 202-204, 245-250, 287 |
| src/kraft/intent.py                          |      198 |        9 |     95% |91-92, 186, 203, 264-265, 321-322, 339 |
| src/kraft/item\_paths.py                     |      323 |       59 |     82% |153, 420, 448, 477-478, 492-495, 507-523, 533-537, 540-542, 559-574, 581-582, 596, 610-613, 697-698, 720 |
| src/kraft/logs.py                            |      292 |        9 |     97% |107, 112, 279, 296, 312-313, 321-322, 448 |
| src/kraft/mcp.py                             |      194 |        2 |     99% |   808-809 |
| src/kraft/mr\_poller.py                      |       68 |        9 |     87% |78-79, 91-92, 98, 133-136 |
| src/kraft/node\_runs.py                      |      104 |        5 |     95% |51, 116-117, 154-155 |
| src/kraft/notify.py                          |      201 |       17 |     92% |185-186, 215-217, 296-303, 316-319, 368-371, 400 |
| src/kraft/outputs.py                         |      265 |       25 |     91% |125, 145-146, 149, 153-154, 158-164, 216-217, 225-226, 297-300, 344, 366, 368, 416-417 |
| src/kraft/overrides.py                       |      120 |        4 |     97% |140, 144, 218-219 |
| src/kraft/paths.py                           |      108 |        0 |    100% |           |
| src/kraft/permission\_hooks.py               |       70 |        1 |     99% |        40 |
| src/kraft/permission\_rules.py               |       40 |        0 |    100% |           |
| src/kraft/pidfile.py                         |      126 |       19 |     85% |51-52, 57-58, 80-81, 119, 136, 147-148, 162-163, 171-172, 174, 198-199, 201-202 |
| src/kraft/policy.py                          |      855 |       25 |     97% |187, 219, 344-345, 674, 679-680, 1395, 1587, 1821-1830, 1910, 1917, 1931, 1935-1939, 1944 |
| src/kraft/proc\_identity.py                  |       44 |        6 |     86% |24-25, 33-34, 96-97 |
| src/kraft/progress.py                        |      195 |        1 |     99% |       389 |
| src/kraft/rate\_limit\_retry.py              |       71 |        8 |     89% |56, 167-169, 190-193 |
| src/kraft/redact.py                          |       46 |        2 |     96% |     47-48 |
| src/kraft/registration.py                    |      119 |        0 |    100% |           |
| src/kraft/remote/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/remote/blobs.py                    |       56 |        0 |    100% |           |
| src/kraft/remote/bundle.py                   |       67 |        7 |     90% |58, 83, 94-95, 116, 131-132 |
| src/kraft/remote/channels.py                 |       65 |       13 |     80% |62-63, 68-71, 74-75, 93-97 |
| src/kraft/remote/check.py                    |       82 |        7 |     91% |94, 130-131, 134-135, 139, 142 |
| src/kraft/remote/daemon.py                   |      151 |        6 |     96% |46-47, 82, 201-202, 204 |
| src/kraft/remote/files.py                    |      143 |        4 |     97% |56, 118, 169-170 |
| src/kraft/remote/forwarder.py                |       41 |        2 |     95% |     61-62 |
| src/kraft/remote/hub.py                      |      343 |       12 |     97% |207-209, 212-213, 289, 324, 399, 421, 428, 564-565 |
| src/kraft/remote/jobs.py                     |      261 |       15 |     94% |172-173, 200-203, 226, 246, 270-271, 323-324, 352-354 |
| src/kraft/remote/launch.py                   |      210 |       16 |     92% |97-98, 113-115, 142-153, 170-171, 221, 224-226, 280, 306, 436 |
| src/kraft/remote/lfs.py                      |      141 |        4 |     97% |119, 212-213, 251 |
| src/kraft/remote/listener.py                 |       76 |        0 |    100% |           |
| src/kraft/remote/local\_jobs.py              |      145 |       17 |     88% |59, 73, 77, 120-121, 135-136, 141-142, 158-160, 186-189, 205 |
| src/kraft/remote/local\_link.py              |      276 |        7 |     97% |89, 317-319, 345, 365, 413 |
| src/kraft/remote/local\_serve.py             |      130 |        7 |     95% |42-43, 74-75, 97-98, 158 |
| src/kraft/remote/machine.py                  |      435 |       42 |     90% |52, 127-130, 144-153, 250, 281-283, 308, 475, 502-504, 507-508, 518-520, 563-564, 672, 678-683, 696-698, 705-710, 731 |
| src/kraft/remote/machine\_config.py          |       23 |        0 |    100% |           |
| src/kraft/remote/paths.py                    |       19 |        0 |    100% |           |
| src/kraft/remote/placement.py                |       85 |        1 |     99% |       120 |
| src/kraft/remote/protocol.py                 |      118 |        4 |     97% |200, 259-260, 268 |
| src/kraft/remote/registry.py                 |      155 |        0 |    100% |           |
| src/kraft/remote/respawn.py                  |       16 |        1 |     94% |        20 |
| src/kraft/remote/runs.py                     |      389 |       18 |     95% |103-104, 142-143, 278-279, 317, 366, 460-461, 502, 517-518, 554-555, 557, 582-583 |
| src/kraft/remote/sandboxed.py                |      145 |       19 |     87% |92, 97, 113-114, 225, 242-244, 261, 274-279, 286, 327-332 |
| src/kraft/remote/sockets.py                  |       27 |        2 |     93% |    31, 33 |
| src/kraft/remote/standin.py                  |       72 |       55 |     24% |44-45, 57-125 |
| src/kraft/remote/transport.py                |       57 |        5 |     91% |36-37, 45, 72-73 |
| src/kraft/remote/workdir.py                  |       86 |        0 |    100% |           |
| src/kraft/render.py                          |      321 |       27 |     92% |63-64, 120, 127, 411, 422, 450-451, 462, 480, 493, 499-503, 554-561, 637, 649, 675 |
| src/kraft/review.py                          |      183 |        5 |     97% |99, 227, 231, 279, 332 |
| src/kraft/review\_reply.py                   |       99 |        7 |     93% |105-106, 138-139, 144, 166, 227 |
| src/kraft/reviewed\_head.py                  |      103 |        2 |     98% |   225-226 |
| src/kraft/skill.py                           |       88 |        8 |     91% |83-84, 113, 199, 204-205, 209-210 |
| src/kraft/slices.py                          |      122 |        0 |    100% |           |
| src/kraft/slices\_docs.py                    |       19 |        0 |    100% |           |
| src/kraft/start\_check.py                    |       39 |        6 |     85% |40-42, 49-50, 58 |
| src/kraft/start\_queue.py                    |      135 |        6 |     96% |94, 139, 282-285 |
| src/kraft/storage.py                         |      304 |       18 |     94% |110-111, 140-141, 176-177, 193-194, 214, 264-271, 416, 551-554 |
| src/kraft/store/\_\_init\_\_.py              |       15 |        0 |    100% |           |
| src/kraft/store/\_common.py                  |       51 |        2 |     96% |  137, 155 |
| src/kraft/store/budget.py                    |       77 |        3 |     96% |   147-153 |
| src/kraft/store/chain.py                     |      273 |        6 |     98% |260, 264, 582, 678-680 |
| src/kraft/store/counters.py                  |       57 |        0 |    100% |           |
| src/kraft/store/forge\_pins.py               |       10 |        0 |    100% |           |
| src/kraft/store/forks.py                     |       43 |        0 |    100% |           |
| src/kraft/store/gates.py                     |       89 |        0 |    100% |           |
| src/kraft/store/intake.py                    |       14 |        0 |    100% |           |
| src/kraft/store/passes.py                    |       62 |        0 |    100% |           |
| src/kraft/store/repos.py                     |       19 |        0 |    100% |           |
| src/kraft/store/review.py                    |      181 |        4 |     98% |212, 217, 224-225 |
| src/kraft/store/sessions.py                  |      214 |        0 |    100% |           |
| src/kraft/store/slices.py                    |      132 |        7 |     95% |126-127, 143-144, 242, 301, 319 |
| src/kraft/store/work\_items.py               |      377 |        6 |     98% |153, 437, 483, 592, 788, 795 |
| src/kraft/subagents.py                       |      132 |        3 |     98% | 30, 34-35 |
| src/kraft/templates/\_\_init\_\_.py          |        0 |        0 |    100% |           |
| src/kraft/templates/catalogue.py             |       86 |        0 |    100% |           |
| src/kraft/templates/environment.py           |      350 |        6 |     98% |549, 551, 617-618, 642-643 |
| src/kraft/templates/extension.py             |      216 |        9 |     96% |68-69, 133, 210, 230, 236, 248, 291-292 |
| src/kraft/templates/forks.py                 |      108 |        0 |    100% |           |
| src/kraft/templates/library.py               |      936 |       24 |     97% |160, 291-292, 340, 347, 536-537, 556, 578-579, 592-593, 643, 652-653, 790, 1259, 1325-1326, 1519, 1544-1546, 1616, 1741 |
| src/kraft/templates/models.py                |     1053 |       10 |     99% |129, 132, 848, 1056, 1376, 2054-2055, 2307, 2361, 2586 |
| src/kraft/templates/one\_off.py              |       82 |        4 |     95% |68, 90, 111, 134 |
| src/kraft/templates/positions.py             |       57 |        0 |    100% |           |
| src/kraft/templates/retry.py                 |      109 |        9 |     92% |196-197, 201, 211-213, 216-217, 224 |
| src/kraft/templates/revision.py              |      323 |       19 |     94% |245, 292, 298, 303-305, 359, 424-425, 434-436, 519, 609, 622, 671-672, 674, 688 |
| src/kraft/templates/slicing.py               |      141 |       12 |     91% |122, 136, 143, 148, 171-172, 183, 225, 228, 231, 258, 296 |
| src/kraft/triggers.py                        |       79 |        6 |     92% |107-108, 148-151 |
| src/kraft/update.py                          |      332 |       18 |     95% |125-129, 134, 219-220, 249-252, 371-372, 374, 475-476, 583, 603, 615 |
| src/kraft/usage.py                           |      489 |       22 |     96% |119-120, 226, 357, 478, 538, 558-559, 562-563, 607, 688, 712-713, 984-985, 1090-1091, 1124-1125, 1275, 1287 |
| src/kraft/vocab/\_\_init\_\_.py              |        8 |        0 |    100% |           |
| src/kraft/vocab/display.py                   |       20 |        0 |    100% |           |
| src/kraft/vocab/events/\_\_init\_\_.py       |       33 |        0 |    100% |           |
| src/kraft/vocab/events/chain.py              |       34 |        0 |    100% |           |
| src/kraft/vocab/events/escalation.py         |       15 |        0 |    100% |           |
| src/kraft/vocab/events/forge.py              |       30 |        0 |    100% |           |
| src/kraft/vocab/events/gate.py               |       26 |        0 |    100% |           |
| src/kraft/vocab/events/limit.py              |       17 |        0 |    100% |           |
| src/kraft/vocab/events/remote.py             |       13 |        0 |    100% |           |
| src/kraft/vocab/events/sandbox.py            |       14 |        0 |    100% |           |
| src/kraft/vocab/events/session.py            |       17 |        0 |    100% |           |
| src/kraft/vocab/events/settings.py           |       15 |        0 |    100% |           |
| src/kraft/vocab/events/traits.py             |        8 |        0 |    100% |           |
| src/kraft/vocab/events/work\_item.py         |       42 |        0 |    100% |           |
| src/kraft/vocab/review.py                    |       22 |        0 |    100% |           |
| src/kraft/vocab/session.py                   |       43 |        0 |    100% |           |
| src/kraft/vocab/sql.py                       |       29 |        0 |    100% |           |
| src/kraft/vocab/stop.py                      |       28 |        0 |    100% |           |
| src/kraft/vocab/total.py                     |       11 |        0 |    100% |           |
| src/kraft/vocab/work\_item.py                |       99 |        0 |    100% |           |
| src/kraft/waits.py                           |      117 |        8 |     93% |286, 359-361, 371-374 |
| src/kraft/when\_free.py                      |      426 |       33 |     92% |77, 156-158, 197-199, 248, 394, 396, 443-445, 561-563, 579-581, 595, 610-616, 624-637, 645, 806 |
| src/kraft/worker/\_\_init\_\_.py             |        0 |        0 |    100% |           |
| src/kraft/worker/backends/\_\_init\_\_.py    |       36 |        0 |    100% |           |
| src/kraft/worker/backends/bwrap.py           |      325 |       26 |     92% |171, 212, 257-260, 275-276, 292, 314, 327, 379, 382, 386, 403, 417, 442, 526-527, 531, 563, 595, 622, 625-627 |
| src/kraft/worker/backends/docker.py          |      741 |       53 |     93% |326, 338-341, 353-360, 437, 552, 600, 609-610, 628, 658, 683-684, 711, 714-717, 786, 1000-1001, 1111-1112, 1139, 1199, 1291-1294, 1387-1388, 1413-1414, 1457, 1521-1522, 1566, 1594, 1663-1664, 1703-1704, 1797-1798, 1826-1827 |
| src/kraft/worker/backends/docker\_forward.py |      136 |        7 |     95% |92-93, 95, 214-216, 224 |
| src/kraft/worker/backends/seatbelt.py        |      150 |        3 |     98% |   365-367 |
| src/kraft/worker/ca.py                       |      108 |        1 |     99% |       306 |
| src/kraft/worker/callback.py                 |       37 |        1 |     97% |        43 |
| src/kraft/worker/channel.py                  |      174 |        7 |     96% |179, 187, 221-222, 323, 349-350 |
| src/kraft/worker/egress.py                   |      518 |       36 |     93% |292-293, 299, 340-342, 361, 398, 404, 420-421, 431, 449, 451, 511, 572, 580-581, 716, 734, 792, 808, 841-842, 844-845, 857, 860, 890-891, 941-946 |
| src/kraft/worker/env.py                      |       11 |        0 |    100% |           |
| src/kraft/worker/host\_view.py               |      203 |       11 |     95% |164, 166-167, 173, 186, 203-204, 209, 213, 328, 340 |
| src/kraft/worker/inject.py                   |      238 |       18 |     92% |194, 214, 230, 241, 253, 351, 365, 374, 384, 390, 393, 407, 410, 415, 424-427 |
| src/kraft/worker/kit.py                      |      421 |       10 |     98% |124, 238-240, 430, 669-670, 672, 698, 732 |
| src/kraft/worker/mounts.py                   |      102 |        2 |     98% |   230-231 |
| src/kraft/worker/os\_init.py                 |      396 |      184 |     54% |75, 85, 89-100, 109-183, 190-205, 224, 245, 247-253, 257-258, 271-272, 292-293, 324-325, 345, 351-353, 365, 393, 448-452, 476-477, 482-545, 549-553, 557 |
| src/kraft/worker/os\_seccomp.py              |       34 |        0 |    100% |           |
| src/kraft/worker/reattach.py                 |      401 |       28 |     93% |120-121, 130-131, 214-215, 341, 387, 399-400, 431, 504, 530-531, 539, 555-556, 625, 669, 809-810, 856, 914-918, 931-932, 949-950 |
| src/kraft/worker/refstore.py                 |      325 |        9 |     97% |210, 213, 222, 426-427, 589-590, 620, 643 |
| src/kraft/worker/sandbox.py                  |      169 |        5 |     97% |134, 178-179, 216, 241 |
| src/kraft/worker/session\_mcp.py             |       23 |        4 |     83% |45, 50-51, 53 |
| src/kraft/worker/shim.py                     |       10 |        0 |    100% |           |
| src/kraft/worker/steering.py                 |       37 |        0 |    100% |           |
| src/kraft/worker/tree\_walk.py               |       99 |       12 |     88% |71-72, 132-133, 135-136, 171-172, 181-182, 185-186 |
| src/kraft/worker/worktree\_read.py           |       90 |        8 |     91% |52, 165-167, 170-174, 182-183 |
| src/kraft/ws.py                              |       72 |        3 |     96% |     77-79 |
| src/kraft/yamlload.py                        |       11 |        0 |    100% |           |
| **TOTAL**                                    | **62611** | **3009** | **95%** |           |


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