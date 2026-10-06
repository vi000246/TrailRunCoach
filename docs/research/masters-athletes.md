# 年長（Masters）跑者：恢復、訓練量與強度要不要依年齡調整（SP-193）

> 調查日期：2026-10-06。只做調查，沒有改程式。基準 commit `47c5a6e`。
> 接續 `periodization-cross-sport.md` §4.3「年長者」[78][80][81][200][312] 與表 22，那裡已有的不重查。最大心率公式接續 `cold-start.md` §3、`zones-and-thresholds.md`。
> 標記：**已驗證（全文）**＝讀到原文；**已驗證（摘要）**＝讀到摘要或摘錄；**搜尋摘要**＝只看到搜尋引擎的轉述，沒讀到原頁；**教練級**＝教練說法，沒有研究；**推估**＝我的延伸；**未找到來源**。
> 本文編號 [M1]、[M2]…，引用舊文件時寫原編號。

## 摘要

1. **app 現在完全不看年齡。** 恢復週（3:1）、加量上限、強度課間隔、賽後恢復天數，25 歲和 60 歲一樣。年齡（出生年）只用在百岳爬坡心率上限，而且用的是 220 − 年齡（`hr_profile.py:377`）。
2. **年紀大確實會退步，但退得比一般人以為的慢。** 持續訓練的人，耐力和最大攝氧量每 10 年約掉 5 %，是不運動者的一半 [M3]；一直維持訓練量的 masters 只掉約 4 % [M2]。最大心率每 10 年約掉 7 下 [M3]，肌肉量每 10 年掉 4–5 % [M3]。
3. **「年長者恢復比較慢」只有部分證據。**
   - 55 km 越野賽後，46 歲組的肌力掉得比 31 歲組多（−40 % 對 −32 %），恢復也比較慢 [M5]。這是唯一一篇越野賽的對照。
   - 但高強度間歇後，表現的恢復沒有年齡差別 [M6][312]；差在**主觀感覺**：56 歲組 48 小時後覺得更累、更痠、更沒動力 [M6]。
   - 年長者對間歇的進步幅度和年輕人一樣 [M8]。
4. **2:1、9 天週期、每週只排一次硬課，都是教練說法，沒有試驗。** Friel 和 Uphill Athlete 自己都沒有附研究 [M10][M11]。沒有任何研究比較 3:1 和 2:1（`periodization-cross-sport.md` §4.3 [198]）。
5. **結論：證據不夠讓 app 依年齡改排課規則。** 年齡只解釋一小部分，個人差異大得多。比較好的方向是**讓每個人的恢復由自己的資料決定**（主觀恢復、賽後恢復期可延長），而不是用年齡切。只有兩件事值得用年齡：最大心率的退路公式要統一成 Tanaka，以及在說明文字裡提到肌力和下坡恢復。建議 4 張單，都是 P3（§5）。

## 1. app 現在怎麼做

### 1.1 年齡在哪裡用到

| 地方 | 用年齡做什麼 | 位置 |
|---|---|---|
| 一般設定的出生年 | 存起來，算出今年幾歲 | `backend/engine/athlete_profile.py:39–46` |
| 百岳爬坡心率上限 | 沒有最大心率時用 0.75 ×（220 − 年齡） | `backend/engine/hr_profile.py:377`、`:386–397` |
| 賽事補給的熱量估算 | 沒填時預設 40 歲 | `backend/engine/racepower/fuel.py:80` |
| 最大心率（課表區間） | **不用**。順序是使用者設定 → COROS 帳號 → 近 365 天跑步最高持續心率 | `backend/engine/hr_profile.py` 的 `max_hr`（`:180–203`） |

排課、加量、恢復、可行性的程式裡都沒有年齡。

### 1.2 單上提到的四個地方

| 規則 | 現在的值 | 位置 | 和年齡的關係 |
|---|---|---|---|
| 恢復週（3:1） | 基礎期連 3 週加量（每週 ≥ 前一週 0.95 倍）→ 第 4 週恢復週，量是前 3 週平均的 65 % | `backend/engine/overview.py:1276–1277`、`:1313–1315` | 沒有 |
| 恢復週上限 | 連續 6 週沒有恢復週就排一週（Koop） | `overview.py:353`、`:384–385` | 沒有 |
| TSB 護欄 | TSB < −30 改恢復週（60 %）、< −20 不加量 | `overview.py:1303–1312` | 沒有 |
| CTL 加量線 | 注意線 10 % CTL、擋線 15 % CTL | `backend/engine/load_guard.py:87–88` | 沒有 |
| 週跑量增幅 | > 20 % 擋、10–20 % 維持 | `load_guard.py:96` | 沒有 |
| 單次長跑上限 | 近 30 天最長的 1.10 倍（Frandsen 2025） | `load_guard.py:107–108` | 研究對象平均 46 歲，本身就偏 masters |
| 強度課間隔 | 5 區每週 ≤ 2 次、硬課間隔 ≥ 2 天、和長跑隔 ≥ 48 小時 | `backend/engine/plan_prefs.py:611–625` | 沒有 |
| 賽後恢復期 | 依賽事大小 7–14 天 | `backend/engine/planning.py:425`（`recovery_plan`，SP-98） | 沒有 |

## 2. 文獻

### 2.1 年紀大，退步多少

| 來源 | 說法 | 標記 |
|---|---|---|
| Tanaka & Seals 2008 [M1] | 耐力表現約 35 歲前維持，50–60 歲前小幅下降，之後加速。主因是最大攝氧量下降；跑步經濟性不隨年齡變差 | 已驗證（摘要） |
| Reaburn & Dascombe 2008 [M2] | masters 的最大攝氧量每 10 年掉 5–46 %，主要看訓練量有沒有減；持續比賽、訓練沒變的人每 10 年只掉 4.1 ± 3.7 % | 搜尋摘要 |
| Hassani 等 2026 [M3] | 有運動的年長者耐力與最大攝氧量每 10 年掉約 5 %，約是不運動者的一半；最大心率每 10 年掉約 7 下；masters 肌肉量每 10 年掉約 4–5 % | 已驗證（全文前段） |
| Lepers & Stapley 2016 [M4] | 40 歲以上參加超耐力賽的人變多，成績進步得比年輕組快；退步幅度依項目和距離不同，騎車退得比跑步和游泳少 | 已驗證（摘要） |

讀完的判斷：**大部分的退步來自練得少，不是年紀本身。** 這支持「看實際訓練量和表現」的做法，app 已經是這樣（CTL、EF、CP 都用個人資料）。

### 2.2 恢復

| 來源 | 對象 | 結果 | 標記 |
|---|---|---|---|
| Easthope 等 2010 [M5] | 55 km 越野賽；10 名年輕（30.5 歲）、13 名 masters（45.9 歲），都訓練有素 | 賽後最大肌力 −32 % 對 −40 %、移動效率 −4.6 % 對 −6.3 %；損傷指標差不多，但「recuperation was slowed in masters」；量到賽後 72 小時 | 已驗證（摘要） |
| Borges 等 2018 [M6] | 6 × 30 秒高強度騎車；9 名 masters（55.6 歲）、8 名年輕（25.9 歲） | 肌力、衝刺、30 分鐘計時、CK 的恢復**沒有**年齡差別；48 小時後 masters 覺得更累（ES 0.75）、更痠（0.61）、更沒動力（0.69） | 已驗證（摘要） |
| Hottenrott 等 2022 [312] | 4 × 30 秒 Wingate；12 名年輕（24.5 歲）、12 名年長（47.3 歲） | 代謝面的恢復沒有年齡差別（只看當下，沒追到隔天） | 已驗證（摘要）；既有文件已引用 |
| Borges 等 2016 回顧 [M7] | — | 年齡對表現和恢復的影響「may be smaller than originally suggested」；久坐比年齡影響大；恢復速度可能有年齡差異，但研究不夠 | 已驗證（摘要） |
| Fell & Williams 2008 回顧 [M7a] | — | 年長者普遍覺得恢復變慢；但多數研究沒有控制活動量，誘發損傷的運動也不像真實訓練 | 已驗證（摘要） |
| Bompa & Buzzichelli 2015 p.66–67 | 教科書 | 年紀大恢復慢、訓練年資長恢復快 | 既有文件已驗證（`bompa-periodization-strength.md`） |

讀完的判斷：

- **會造成肌肉損傷的課（長下坡、越野賽、超長天）**，masters 恢復比較慢，有一篇越野賽的直接證據 [M5]。樣本只有 13 人，年齡也只到 46 歲。
- **高強度間歇**，表現的恢復沒有年齡差別 [M6][312]。差在主觀感覺，所以「年長者需要多休」有一部分是感覺，不一定是能力。
- 兩件事加起來：如果要調整，**該調的是賽後和大下坡後的恢復，不是間歇的間隔**。

### 2.3 能不能練得起來

| 來源 | 說法 | 標記 |
|---|---|---|
| Støren 等 2017（MSSE）[M8] | 8 週高強度間歇，20 歲到 70 歲以上各組的最大攝氧量都進步 9–13 %，各年齡組沒有差別 | 搜尋摘要（原文沒讀到） |
| Hassani 2026 [M3] | 建議耐力＋肌力＋爆發力的混合訓練，依個人能力調整 | 已驗證（全文前段） |

### 2.4 受傷型態

| 來源 | 對象 | 結果 | 標記 |
|---|---|---|---|
| McKean 等 2006 [M9] | Hood to Coast 接力 2,886 人，40 歲以上佔 34 % | masters 受傷比例較高、多處受傷較多；小腿和大腿後側的傷比年輕人多 | 已驗證（摘要） |
| Knobloch 等 2008 [M9a] | 291 名平均 42 歲的跑者，每週 65 km | 阿基里斯腱病最常見；跑齡 > 10 年的人阿基里斯腱病風險 RR 1.6 | 已驗證（摘要） |
| 越野跑者受傷系統回顧（Jiang 等）[M9b] | 越野跑者 | 膝和踝最多；年齡只列為個人因素，**沒有依年齡分組的資料** | 已驗證（全文摘錄） |

讀完的判斷：年長者多的是**小腿、阿基里斯腱、大腿後側**這類軟組織傷。這和肌力流失一致（§2.1），是「年長者更要做肌力」的理由。越野跑者沒有分年齡的資料。

### 2.5 教練怎麼說（都沒有附研究）

| 來源 | 說法 | 標記 |
|---|---|---|
| Friel〈Designing a Microcycle to Match Your Recovery〉[M10] | 9 天週期：每 3 天一次硬課，中間 2 天恢復（可以是休息、輕鬆或交叉訓練；「cross training is especially good for senior runners」）；有人要 3 天。文章沒有引用研究 | 已驗證（全文） |
| Friel《Fast After 50》（TrainingPeaks 書摘）[M10a] | 高強度間歇每週一次，或每 9 天一次更好 | 已驗證（全文） |
| Uphill Athlete〈The 5+2 Framework〉[200] | 50 歲以上：每週 5 天低強度＋2 天硬課，硬課之間至少 2 天；「Three or four hard sessions per week … produces injury and accumulated fatigue faster than it produces adaptation」；硬課間隔 48–72 小時；先肌力、再有氧、最後專項強度。沒有引用研究 | 已驗證（全文） |
| Friel〈Aging: Customizing the Peak Period〉[81] | 自己說年長者的建議沒有研究 | 既有文件已驗證 |
| 搜尋摘要轉述 Pfitzinger | 「50 歲以上要多 35–60 % 恢復時間」 | **查無出處、不採用**（只在一個工具網站的整理裡，沒有書頁） |

讀完的判斷：教練一致的地方是**每週硬課最多 2 次、間隔 48–72 小時**。app 現在就是「5 區每週 ≤ 2 次、間隔 ≥ 2 天」（`plan_prefs.py:611–625`），已經符合。不一致的是「要不要再更少」（Friel 的 9 天、每週一次），這部分沒有證據。

### 2.6 最大心率的年齡公式

| 公式 | 來源 | 誤差 | 標記 |
|---|---|---|---|
| 220 − 年齡 | 長野縣 Safety Book（app 百岳上限用的） | 對 30 歲以上會低估 | 既有文件已驗證（`mountaineering-physiology-scholars.md`） |
| 208 − 0.7 × 年齡 | Tanaka 2001 [C3] | Ausland 2026：平均低估 4.8 下，一致性界限 −18.5～+9.1 | 既有文件已驗證（`cold-start.md`、`zones-and-thresholds.md`） |
| 211 − 0.64 × 年齡 | Nes 2013 [M12]（HUNT，3,320 人） | 標準誤 10.8 下；和性別、活動量、體能沒有交互作用；舊公式低估 30 歲以上 | 已驗證（摘要） |

60 歲的人：220 − 年齡 = 160，Tanaka = 166，Nes = 173。差 6–13 下。百岳上限乘 0.75 後差 5–10 下。

## 3. 落差

| # | 項目 | 來源怎麼說 | app 現在 | 判斷 |
|---|---|---|---|---|
| 1 | 恢復週頻率 | 教練說 masters 用 2:1 或每 3 週一次；沒有試驗 [78][200][198] | 一律 3:1 | **不依年齡改**。沒有證據，而且 app 已有 TSB 護欄和 6 週上限。若要給，做成使用者自選 |
| 2 | 強度課間隔 | 每週 ≤ 2 次、間隔 48–72 小時（UA，教練級） | ≤ 2 次、間隔 ≥ 2 天 | 一致 |
| 3 | 間歇後恢復 | 表現的恢復沒有年齡差別 [M6][312] | 不分年齡 | 一致 |
| 4 | 越野賽、大下坡後恢復 | masters 恢復較慢 [M5]（n = 13） | 依賽事大小 7–14 天，不看年齡；SP-111 依下坡比值升一級 | **小缺口**：可以讓使用者自己延長，不用年齡自動延長 |
| 5 | 主觀恢復 | 年長者主觀恢復較慢 [M6] | 沒有每天的主觀恢復輸入 | 和 `readiness-signals.md` 同一件事，不另開 |
| 6 | 最大心率退路 | Tanaka／Nes 比 220 − 年齡準 | 百岳上限用 220 − 年齡；課表區間沒有年齡退路（`cold-start.md` T2 建議 Tanaka） | **不一致**：同一個 app 兩個公式 |
| 7 | 肌力 | masters 肌肉量每 10 年 −4–5 % [M3]；軟組織傷較多 [M9] | 每週 2 次肌力，不分年齡（`status.py:61`） | 量夠；說明文字沒提年齡 |
| 8 | 加量速度 | 沒有找到依年齡的加量研究 | 不分年齡 | 未找到來源，不改 |

## 4. 結論

- **不建議依年齡改排課規則。** 理由有三：
  1. 唯一直接比較恢復的越野研究只有 13 名 masters，平均 46 歲 [M5]；間歇後的研究反而沒有差別 [M6][312]。
  2. 退步主要來自練得少 [M2][M3]，app 本來就用個人的 CTL、EF、CP 追蹤，年齡沒有提供更多資訊。
  3. app 要給所有人用（記憶：「App 給所有人用」），用年齡切規則會讓同年齡、體能差很多的人拿到一樣的限制。
- **可以做的是讓每個人自己調。** 賽後恢復期可以手動延長；恢復週頻率可以自選。這樣年長者、新手、恢復比較慢的人都適用。
- **該統一的是最大心率公式。** 百岳上限改用 Tanaka，和 `cold-start.md` T2 用同一個退路。
- **該補的是說明文字。** 肌力、小腿與阿基里斯腱、下坡後恢復，這三件事對 40 歲以上更重要，可以在說明裡提，不改規則。

## 5. 建議開的單

| # | 標題 | 優先度 | 驗收條件（草案） | 要你決定的事 |
|---|---|---|---|---|
| M-1 | 百岳爬坡上限的年齡退路改用 Tanaka（208 − 0.7 × 年齡） | P3 | (a) `hr_profile.walk_cap` 沒有最大心率時改用 208 − 0.7 × 年齡；(b) 和 `cold-start.md` T2 的最大心率退路共用同一個函式；(c) 來源文字寫 Tanaka 2001，標推估、個人誤差可到 ±10 下以上；(d) 測試：60 歲 → 0.75 × 166 = 124.5 | 要用 Tanaka 還是 Nes（Nes 對年長者更高，但只有挪威一個母體）。建議 Tanaka，因為 T2 已經選它 |
| M-2 | 恢復週頻率變成課表偏好（3:1 預設，可選 2:1） | P3 | (a) 課表偏好新增「恢復週頻率」：3:1（預設）／2:1；(b) 2:1 時 `build3` 改成看 2 週；(c) `RECOVERY_MAX_GAP` 跟著縮短（2:1 → 3 週）；(d) 說明文字寫「沒有研究比較兩者；有些教練建議 50 歲以上或恢復較慢的人用 2:1」，不寫年齡門檻；(e) 未來週推估（`projection`）一致；(f) 有測試 | 要不要做。證據是教練級，做的話是給使用者一個選項，不是建議 |
| M-3 | 賽後恢復期可以手動延長 | P3 | (a) A／B 賽的恢復期在課表上可以「延長 3／7 天」；(b) 延長後回量期、轉換期順延，不吃掉下一場 A 賽的專項期；(c) 說明文字寫「長下坡或超馬後，部分跑者（特別是 40 歲以上）恢復較慢」，引 Easthope 2010，不自動依年齡延長 | 延長的上限（建議 7 天）；和 SP-111 的「下坡比值升一級」要不要合併成同一個入口 |
| M-4 | 肌力與小腿的說明文字補上年長者的理由 | P3 | (a) 肌力課說明加一句「40 歲以上肌肉量每 10 年約少 4–5 %，小腿、阿基里斯腱、大腿後側的傷較多」，引 Hassani 2026、McKean 2006；(b) 只改文字，不改次數 | 要不要只在有填出生年、≥ 40 歲的人顯示，還是所有人都顯示 |

**不建議開的單**

- 依年齡自動改 3:1、加量線、強度課間隔：沒有試驗支持（§2.5、§3 第 1、8 項）。
- 依年齡降低每週強度課次數：app 已經是每週 ≤ 2 次，和 UA 的 masters 建議一樣。

## 6. 限制、付費牆、查無出處

**限制**

- 恢復的研究都很小（每組 8–13 人），masters 平均 46–56 歲，**沒有 60 歲以上的越野研究**。
- 沒有找到年長**越野**跑者的受傷資料，McKean 和 Knobloch 都是路跑。
- 沒有找到比較 3:1 和 2:1 的研究，也沒有找到依年齡的加量研究。
- 沒有讀 `planning.recovery_plan` 的全部程式，只確認它不看年齡。

**付費牆、沒讀到原文**

- Easthope 2010 全文（Springer 要登入）：只讀到摘要，沒有 24、48、72 小時各項目的數字。
- Tanaka & Seals 2008、Reaburn & Dascombe 2008、Fell & Williams 2008 全文：只讀到摘要或搜尋摘要。
- Hottenrott 2022、Støren 2017 的全文頁被驗證碼擋住（PMC）。
- McKean 2006 全文：只讀到摘要，沒有各部位的百分比。

**查無出處、不採用**

- 「50 歲以上要多 35–60 % 恢復時間」（轉述 Pfitzinger）：只在工具網站，沒有書頁。
- 「55 歲跑完全馬要 45 天以上、25 歲 26 天」：同一個來源，不採用。

## 參考

- [M1] Tanaka H, Seals DR. Endurance exercise performance in Masters athletes: age-associated changes and underlying physiological mechanisms. *J Physiol* 2008;586(1):55–63. https://doi.org/10.1113/jphysiol.2007.141879
- [M2] Reaburn P, Dascombe B. Endurance performance in masters athletes. *Eur Rev Aging Phys Act* 2008;5(1):31–42. https://research.bond.edu.au/en/publications/endurance-performance-in-masters-athletes/ （搜尋摘要）
- [M3] Hassani M, Renzini A, Nguyen L, Coletti D. Changes in physical performance with aging in master athletes and in the general population: an update. *Eur J Transl Myol* 2026. https://doi.org/10.4081/ejtm.2026.14884 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC13389671/
- [M4] Lepers R, Stapley PJ. Master athletes are extending the limits of human endurance. *Front Physiol* 2016;7:613. https://doi.org/10.3389/fphys.2016.00613
- [M5] Easthope CS, Hausswirth C, Louis J, et al. Effects of a trail running competition on muscular performance and efficiency in well-trained young and master athletes. *Eur J Appl Physiol* 2010;110:1107–1116. https://doi.org/10.1007/s00421-010-1597-1 （PMID 20703499）
- [M6] Borges NR, Reaburn PR, Doering TM, Argus CK, Driller MW. Age-related changes in physical and perceptual markers of recovery following high-intensity interval cycle exercise. *Exp Aging Res* 2018. https://doi.org/10.1080/0361073x.2018.1477361
- [M7] Borges N, Reaburn P, Driller M, Argus C. Age-related changes in performance and recovery kinetics in masters athletes: a narrative review. *J Aging Phys Act* 2016. https://researchprofiles.canberra.edu.au/en/publications/age-related-changes-in-performance-and-recovery-kinetics-in-maste/
- [M7a] Fell J, Williams D. The effect of aging on skeletal-muscle recovery from exercise: possible implications for aging athletes. *J Aging Phys Act* 2008;16:97–115. https://journals.humankinetics.com/view/journals/japa/16/1/article-p97.xml
- [M8] Støren Ø, Helgerud J, Sæbø M, et al. The effect of age on the VO2max response to high-intensity interval training. *Med Sci Sports Exerc* 2017. https://doi.org/10.1249/MSS.0000000000001070 （搜尋摘要）
- [M9] McKean KA, Manson NA, Stanish WD. Musculoskeletal injury in the masters runners. *Clin J Sport Med* 2006. https://pubmed.ncbi.nlm.nih.gov/16603885/
- [M9a] Knobloch K, Yoon U, Vogt PM. Acute and overuse injuries correlated to hours of training in master running athletes. *Foot Ankle Int* 2008. https://doi.org/10.3113/fai.2008.0671 （PMID 18785416）
- [M9b] Characteristics of lower limb running-related injuries in trail runners: a systematic review. *Phys Act Health*. https://doi.org/10.5334/paah.375
- [M10] Joe Friel. Aging: Designing a Microcycle to Match Your Recovery. https://joefrieltraining.com/aging-designing-a-microcycle-to-match-your-recovery/ （2026-10-06 讀取）
- [M10a] Fast After 50: HIIT for Seniors. TrainingPeaks. https://www.trainingpeaks.com/blog/fast-after-50-high-intensity-interval-training-and-the-aging-athlete/
- [M12] Nes BM, Janszky I, Wisløff U, et al. Age-predicted maximal heart rate in healthy subjects: the HUNT Fitness Study. *Scand J Med Sci Sports* 2013. https://doi.org/10.1111/j.1600-0838.2012.01445.x
- 舊文件編號：[78][80][81][198][200][312] 見 `periodization-cross-sport.md` 參考文獻；[C3] Tanaka 2001 見 `cold-start.md`；Ausland 2026 見 `zones-and-thresholds.md`。
