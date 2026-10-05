# 負重訓練（Loaded Carry）：百岳多日行程要不要練、怎麼排、怎麼做

> **2026-10-02 更新：app 已經不排負重課了（使用者決定）。**
>
> **改成什麼：**
> - `engine/loaded_carry.py` 和總覽的「負重訓練」卡都拿掉了。
> - 取而代之的是 `engine/steep_hill.py`：專項期（賽前第 10–3 週）每週把一次平日的輕鬆跑換成「陡坡健走（模擬負重）」，**不背包**。
>
> **坡度怎麼來：**
> - 用 §1.1 的 Pandolf 公式，算出「不背包、走更陡」的坡度，讓代謝量等於背包時。
> - 背包時的條件取 §3.2 的機器課：12% 坡、3.5 km/h。
> - 重量照 5% → 10% 體重 → 行程背包的進度：例如背 5% 體重時是 13%，背 9 kg（70 kg 的 13%）時是 14%。
> - 坡度超過 15% 時，坡度停在 15%，改成加快速度。
>
> **其他也跟著改：**
> - B2B 第 1 天不再背包。
> - 範本的「負重爬坡」換成三份「陡坡健走」。
>
> **還保留的：**
> - 每次活動的背包重量（`racepower_hike_meta.json`），百岳預測會用到。
> - 行程背包（`Event.pack_kg`）。
>
> **背包才練得到的部分（§1.2：髖、軀幹、腳底、背著下坡）現在沒有排。**
> 下面是當時的研究內容，保留作為依據。

- 日期：2026-10-02
- 分支：`docs/loaded-carry`（只寫文件，不改程式，不寫 DB）
- 相關文件：`back-to-back-and-long-day.md`（B2B、§2.5 背包進度）、`baiyue-from-running.md`（Pandolf、Ludlow & Weyand、Looney）、
  `racepower-v2.md` §3C、`uphill-athlete-mountain-metrics.md`（機器爬升 50 % 折扣、負重 TSS 項）、
  `vo2max-gate-and-trail-metric.md`（ME、下坡離心）、`interval-prescription.md`；
  程式（唯讀）：`backend/engine/racepower/hike.py`、`capacity.py`、`hikehr.py`、`athlete.py`、
  `backend/engine/planning.py`、`overview.py`、`plan_prefs.py`、`quality_gate.py`、`backend/api/racepower.py`
- 標記：**同儕審查**、**教練經驗**、**廠商**、**推估**（沒有來源的數字或做法）、
  **未驗證**（只有搜尋摘要，或沒能逐字核對）。徐國峰算來源。
- 引文：論文只讀了摘要（Europe PMC REST API）；Orr 2021 讀了 PMC 全文的部分段落（WebFetch 抽取）。
  教練網頁的引文是 WebFetch 工具從原頁抽出的句子，**不是我逐字比對過的**，引用時當成「抽取的原句」。

---

## 摘要

1. **要練，但份量不大。** 範例跑者的條件是 9 kg、2 天、跟團。
   - 9 kg 約是體重的 13 %（範例跑者 70 kg）。軍方研究裡的負重是 20–45 kg，比這重很多。
   - **代謝上**，9 kg 只是讓「同樣心率下爬得慢約 10 %」（Pandolf，§1.1）。這部分不背包、走得更陡更快也練得到。
   - **真正只有背包練得到的**，是組織和動作：髖伸肌、軀幹和肩膀、腳底和水泡，還有背著重量下坡的離心負荷。
2. **證據：**
   - 系統性回顧和統合分析（Knapik 2012，**同儕審查**）：訓練裡有「漸進的負重行軍」時，效果量 1.7 SD。
     有氧加肌力、每週 ≥ 3 次、≥ 4 週，效果量 ≥ 0.8；只做有氧或只做肌力，效果比較小，也比較不穩定。
   - Rudzki 1989：跑步組的 VO2max 進步比較多，負重行軍組卻被評為「比較能應付任務」。
   - 受傷風險隨背負比例、頻率上升：> 25 % 體重 OR 2.09；每月 ≥ 5 次 OR 2.11（Schuh-Renner 2017）。
     漸進式的負重計畫反而讓受傷變少：RR 0.68，疲勞性骨折 8 % → 3 %（Kelly 2024）。
3. **怎麼排（推估，綜合 UA、Orr 2021、Schuh-Renner 2017）：**
   - 基礎後期：先做肌耐力（ME）的健身房版，不背包或很輕。
   - 專項期（賽前第 10–3 週）：背包 5 % → 10 % → 行程背包（9 kg），每階段約 2 週。
   - 頻率：負重課每 28 天最多 4 次；背包長天每 10–14 天最多 1 次。平日用 40–50 分的跑步機坡度或樓梯機背包課補。
   - 訓練裡最重就是行程背包。短的機器課可以到 1.15 倍，但不超過 20 % 體重。
   - 減量期：最後一次背包長天在賽前 ≥ 14 天；賽前 7 天內不背包。
4. **app 怎麼做：**
   - `Event` 加 `pack_kg`。沒填時用 `capacity.PACK_DEFAULT_MULTI` 的 9 kg。
   - 每次訓練背多少，用現有的 `racepower_hike_meta.json`（`athlete.set_hike_meta`）記錄。
   - 判斷做得好不好：
     - 同一段坡度下，比較背包和不背包的「同心率 VAM」，再除以 Pandolf 預測的比例，得到「負重效率」；
     - 同一個專項期裡，看這個值有沒有變好。
     - `hikehr` 現在只收心率 ≥ AeT 的窗，要另外開一個 AeT 附近的心率帶。

---

## 1. 生理與專項性

### 1.1 背負上坡的代謝成本（Pandolf）

app 已經用 Pandolf, Givoni & Goldman 1977（*J Appl Physiol* 43:577–581，DOI 10.1152/jappl.1977.43.4.577，**同儕審查**；
題名是〈Predicting energy expenditure with loads while standing or walking very slowly〉）：

```text
M = 1.5W + 2.0(W+L)(L/W)² + η(W+L)(1.5V² + 0.35·V·G)      （W、L kg；V m/s；G %）
```

- 實作在 `racepower/hike.py:28–41`（`pandolf`）；`capacity.py` 的 B2／B3 用它的反函數算「同代謝率的速度」。
- 只用在上坡和平地。下坡修正（Santee 2003）只有單一來源，app 拒絕用（`hike.py:36–38`）。
- 重負重時 Pandolf 偏低（Looney 2022，轉引自 `baiyue-from-running.md`）。

**用 app 的程式碼算範例跑者（70 kg，`capacity.aet_power`，AeT 時的跑速 2.4–2.8 m/s，推估輸入）：**

| 坡度 | 背負 | 同 AeT 代謝率的 VAM（m/h） | 比不背包 | 線性 (W)/(W+L) |
|---|---|---|---|---|
| 20 % | 0 | 730–831 | 1.000 | 1.000 |
| 20 % | 3.5 kg（5 %） | 700–798 | 0.96 | 0.952 |
| 20 % | 7 kg（10 %） | 672–766 | 0.92 | 0.909 |
| 20 % | **9 kg（13 %）** | 654–746 | **0.90** | 0.883 |
| 20 % | 14 kg（20 %） | 618–707 | 0.85 | 0.833 |
| 30 % | 9 kg | 714–823 | 0.89 | 0.883 |

- 固定速度時（20 %、0.6 m/s），9 kg 讓代謝率約 +10.7 %；14 kg 約 +16.7 %。
- 這是 `capacity.pack_ratio`／`prior_speed` 的輸出（指令放在 scratchpad，沒有進 repo）。數字依賴 Pandolf 和 app 自訂的 AeT 功率先驗，所以標**推估**。

**意思是：**

- 對心肺來說，9 kg 和「多 9 kg 體重」差不多：Ludlow & Weyand 2017 的摘要寫，步行代謝量與總重（體重 + 背負）成正比（轉引自 `baiyue-from-running.md` §2，**同儕審查**）。
- 所以**有氧的部分不需要背包**。不背包、爬陡一點或快一點，心率一樣可以到 AeT。
- 背包帶來的額外刺激主要不在心肺，而在 §1.2 的組織和動作。

### 1.2 背包訓練「專項」在哪裡

| 層面 | 證據 | 類別 |
|---|---|---|
| 髖伸肌負荷 | Xu 2017（*BMC Musculoskelet Disord* 18:125，DOI 10.1186/s12891-017-1481-9）：女性背 30 % 體重跑步時，髖關節反作用力平均增加 49.1 % 體重（膝 20.6 %、踝 4.8 %）；「hip extensor muscles … are the main power generators」。步行和跑步每一步的累積脛骨應力差不多（13.6 vs 15.2 MPa·s） | 同儕審查（模型研究，跑步為主） |
| 上坡的踝、膝 | Lee, Yoon & Shin 2017（*J Appl Biomech* 33:397–405，DOI 10.1123/jab.2016-0221）：15° 上坡背包時，支撐時間縮短、內外側衝量增加、踝內外翻和背屈／蹠屈活動度增加，作者解讀為重心升高、身體比較不穩 | 同儕審查 |
| 軀幹、肩、主觀負荷 | Simpson, Munro & Steele 2011（*Appl Ergon* 42:403–410，DOI 10.1016/j.apergo.2010.08.018）：15 位女性休閒健行者 8 km、自選速度，0／20／30／40 % 體重。**心率沒有顯著差異**；20 % 已經讓軀幹姿勢、RPE、肩膀不適明顯改變；建議上限 30 % | 同儕審查 |
| 下坡離心 | Blacker 2010（*Aviat Space Environ Med* 81:745–753，DOI 10.3357/asem.2659.2010）：25 kg、2 小時、6.5 km/h，平地和 −8 % 坡；股四頭肌等長力量立刻掉 15–16 %，72 小時才完全恢復 | 同儕審查 |
| 連續多天 | Straight 2025（*Physiol Rep* 13:e70268，DOI 10.14814/phy2.70268）：士兵 3 次負重，力量下降（膝屈肌、軀幹伸肌最多），每次都有發炎反應 | 同儕審查 |

- Simpson 2011 的「心率沒有差異」值得注意。自選速度時，人會自己放慢。所以**背包的代價出現在速度、姿勢和主觀感受上，不一定出現在心率上**。這會影響 §5.4 的判讀。
- 除了 Xu 2017 是跑步模型，其他都是走路。

### 1.3 負重訓練是否比不負重訓練更能提升負重表現

| 研究 | 發現 | 類別 |
|---|---|---|
| **Knapik, Harman, Steelman & Graham 2012**，*J Strength Cond Res* 26:585–597，DOI 10.1519/jsc.0b013e3182429853，PMID 22130400（系統性回顧＋統合分析，10 篇） | 「large training effects (≥0.8SD units) were apparent when progressive resistance training was combined with aerobic training and when that training was conducted at least 3 times per week, over at least 4 weeks. When progressive load-carriage exercise was part of the training program, much larger training effects were evident (summary effect size [SES] = 1.7SD units)」；「Aerobic training alone or resistance training alone had smaller and more variable effects」 | 同儕審查 |
| **Rudzki 1989**，*Mil Med* 154:201–205，DOI 10.1093/milmed/154.4.201 | 新兵 11 週：VO2max 跑步組 +11.9 %、負重行軍組 +8.9 %；負重組「was viewed as being better able to cope with military tasks」；兩組因傷病淘汰都是 49 % | 同儕審查（主觀評分） |
| **Wills 2019**，*J Strength Cond Res* 33:2338–2343，DOI 10.1519/jsc.0000000000003243 | 15 位男性平民，10 週肌力＋負重步行；23 kg 背心 5 km 的 RPE 明顯下降，估計 VO2max 上升 | 同儕審查（無對照組） |
| **Wills 2023**，*Mil Med* 188:658–664，DOI 10.1093/milmed/usab470 | 同一計畫，男 15、女 13；兩性的心率與 RPE 反應都下降；男性有氧能力 +5.4 %，女性沒有 | 同儕審查（無對照組） |
| **Orr 2019**，*Int J Exerc Sci* 12:1001–1022，DOI 10.70252/ovvg8388，PMC6719820（critical review，16 篇） | 下肢肌力與爆發力「can predict load carriage performance」 | 同儕審查 |
| **Orr et al. 2021**，*IJERPH* 18:4010，DOI 10.3390/ijerph18084010，PMC8069713（全文部分段落） | 「Research shows that load carriage-specific training is optimal to improve load carriage performance」；「resistance training (notably relative strength based) and aerobic conditioning should form part of the load carriage conditioning」 | 同儕審查（敘述性回顧） |
| Robinson 2018（轉引自 `baiyue-from-running.md`） | 警察 25 kg 5 km，有氧能力與成績的相關最高（r ≈ −0.71） | 同儕審查（沒有重新核對） |

**結論：**

- 「有負重課 > 沒有負重課」的方向，在軍方文獻裡相當一致（Knapik 2012 的 1.7 vs ≥ 0.8 SD）。
- 但有三個限制：
  1. 負重是 20–25 kg 以上，約 25–35 % 體重，是你的兩倍以上。
  2. 測驗是「背著跑完固定距離的時間」，不是多日爬升。
  3. Knapik 2012 的 10 篇只有摘要可讀，各篇的負重劑量**沒有讀到**。
- 推到 9 kg、2 天、跟團的百岳，方向應該一樣，但**效果會小很多**（**推估**）。
  原因是 13 % 的代價大部分是代謝，而代謝不用背包也練得到（§1.1）。
- 沒有找到休閒健行者或越野跑者的負重訓練介入研究（Europe PMC 搜尋 hiking／trekking／mountaineering + training intervention，找不到）。

### 1.4 受傷風險

| 研究 | 發現 | 類別 |
|---|---|---|
| **Schuh-Renner 2017**，*J Sci Med Sport* 20 Suppl 4:S28–S33，DOI 10.1016/j.jsams.2017.07.027（831 位步兵，回溯） | 負重行軍每英里的受傷風險比跑步高（RR 1.8）；背 > 25 % 體重 OR 2.09；每月 ≥ 5 次 OR 2.11；**個人訓練每週跑 < 4 英里** OR 3.56–4.14；累積「重量 × 距離」越高，風險越高 | 同儕審查 |
| **Kelly 2024**，*Work* 77:1391–1399，DOI 10.3233/wor-230569 | 美國陸戰隊新兵，改用週期化負重計畫：受傷比例 58 % → 39 %（RR 0.68）；疲勞性骨折 8 % → 3 %，但壓力反應 0.3 % → 6 %；部位前四名是膝、小腿、踝、足 | 同儕審查（回溯、非隨機） |
| **Stoltzfus 2022**，*Wilderness Environ Med* 33:59–65，DOI 10.1016/j.wem.2021.11.010 | 1206 位童軍 12 天健行，7 % 受傷；和受傷有關的是「greater backpack weight to body weight ratio」、BMI > 30、過去受傷 | 同儕審查（健行族群） |
| **Orr, Pope, Johnston & Coyle 2014**，*Int J Inj Contr Saf Promot* 21:388–396，DOI 10.1080/17457300.2013.833944 | 下肢最常受傷：水泡、疲勞性骨折、膝和足痛、神經病變；肩（背包麻痺）和下背也不少 | 同儕審查（敘述性回顧） |
| **Orr 2015**，*J Occup Rehabil* 25:316–322，DOI 10.1007/s10926-014-9540-7 | 澳洲陸軍 404 件負重傷害，多數是下肢或背；機轉一半以上是 muscular stress；「Physical training may fail to adequately prepare soldiers for load carriage tasks」 | 同儕審查 |
| **Knapik, Reynolds & Harman 2004**，*Mil Med* 169:45–56，DOI 10.7205/milmed.169.1.45 | 常見傷害：水泡、疲勞性骨折、背部拉傷、蹠骨痛、背包麻痺、膝痛；用腰帶可以減少肩膀壓力；腳上每多 1 kg，能量消耗 +7–10 % | 同儕審查（回顧） |
| Orr 2021（PMC 全文） | 疲勞性骨折常見部位：骨盆、脛骨、跟骨、蹠骨；負重後軀幹和四肢神經肌肉功能要 48–72 小時才恢復 | 同儕審查 |

**對你：**

- 9 kg ≈ 13 %，離 > 25 % 的風險門檻很遠（Schuh-Renner）。Simpson 的 20 % 已經會改變姿勢。所以訓練背包不要超過 20 % 體重（≈ 13.6 kg）。
- 風險主要來自**頻率和累積量**：每月 ≥ 5 次、重量 × 距離。所以 §2.2 限制的是次數，不是重量。
- 「每週跑 < 4 英里」風險高三倍以上。表示跑步底子本身有保護作用（相關，不是因果）。對有跑步底子的人，這是好消息。
- 登山肌力的心得文也提到：下坡膝痛和肌肉疲勞、補給不足有關（筆記轉述，**未驗證**）。

---

## 2. 怎麼排

### 2.1 什麼時候做

| 訓練期 | 負重課 | 依據 |
|---|---|---|
| 基礎期前段 | 不背包。爬坡和長天照 app 現在的排法 | UA trekking：「Weeks 1–4: Bodyweight only」；UA mountaineering：加重量前至少 4 週、16 次有氧課 |
| 基礎後期（賽前第 14–11 週） | ME 健身房版（§3.4），不背包 → 10 % 背心。外面的 ME 負重爬坡可以選 | UA ME 頁：「best placed during the late base period and into the sport-specific preparation period」；Johnston（Evoke）：登山者在減量前最後 8–12 週（至少 6 週） |
| 專項期（賽前第 10–3 週，`planning.py` 減量 14 天 + 專項 8 週） | 背包進度 5 % → 10 % → 行程背包；B2B 第 1 天背包（`back-to-back-and-long-day.md` §2.5） | UA trekking 進度；Koop／CTS：「train … the most race specific aspects closer to their events」 |
| 減量期（賽前 14 天） | 賽前第 14–8 天最多一次 30 分短課背行程背包；賽前 7 天內不背 | UA mountaineering：「For a climb of 2-3 days, a one week taper is enough」，量 −50 %；UA trekking：「reduce training volume but maintain the intensity」；Blacker 2010 恢復 72 小時；肌力週期化筆記（轉述）：長距離耐力項目「比賽前2周就可停止肌力訓練」 |
| 恢復期 | 不背包 | — |

- 江晏慶的「強化期不要忘了肌力」、「肌力一周做2次」也支持專項期保留肌力（筆記轉述）。
- 肌力週期化的筆記（轉述）寫「越野跑是長時肌耐力」，最大肌力之後要轉換到專項肌耐力。這正好對應「基礎期肌力 → 基礎後期 ME → 專項期背包」的順序。

### 2.2 多常做

| 來源 | 頻率 | 類別 |
|---|---|---|
| Orr 2021（全文） | 「a load carriage-specific session should be conducted at least once every seven to 14 days」（引 Rudzki 1997、Knapik 2012） | 同儕審查（回顧） |
| Orr 2021（全文） | 「with an increased risk of injury and no additional improvements in load carriage performance found when sessions were greater than four per month …, the recommendation is that load carriage-specific sessions are conducted no more than once every 10 to 14 days」（引 Hauschild 2016，美國陸軍公衛中心技術報告，**沒有讀原文**） | 同儕審查（回顧）＋技術報告 |
| Schuh-Renner 2017 | 每月 ≥ 5 次 OR 2.11 | 同儕審查 |
| UA ME 頁 | 「One ME session per week is typical」，每次 ME 之後「at least three days of easy recovery workouts」 | 教練經驗 |
| UA〈Exercises for Muscular Endurance〉 | 「only one Muscular Endurance workout every 7-10 days」；職業選手可以 3–4 天 | 教練經驗 |
| UA mountaineering | Rainier 等級：「at least one mountain climbing workout per week where you ascend 4,000 vertical feet … with a backpack of approximately the same weight」 | 教練經驗 |

**建議（推估，把上面組合起來）：**

- **每 28 天最多 4 次負重課**（Orr／Schuh-Renner 的「每月 > 4 次沒有好處、風險較高」）。
- **背包長天**（≥ 2 小時、有背包）**每 10–14 天最多 1 次**（Orr 2021 的上限）。
- 剩下的名額給平日的短課（樓梯機、跑步機坡度，§3.2），隔週輪流：
  - A 週：週末背包長天（或 B2B）；
  - B 週：週末不背包的長天，加一次平日 40–50 分背包機器課。
- 一個 3:1 週期的 3 個加量週：A、B、A 或 B、A、B。恢復週不背包。
- ME 健身房版（§3.4）算在肌力課裡，**不算**負重課的次數（它不是背著走路）。外面的 ME 負重爬坡要算。

### 2.3 怎麼進步：重量、時間、爬升

**UA 的進度（教練經驗，WebFetch 抽取的原句）：**

- trekking（Steve House，2025-05-20）：「Weeks 1–4: Bodyweight only / Weeks 5–6: Add 5% body weight / Weeks 7–8: Add 10% body weight / Weeks 9–10: Add 15–20% body weight」；上限「up to 20% of your body weight or up to the maximum weight you will carry on your trek」；新手比行程輕 10–15 %。
- mountaineering（Steve House，2021-03-14）：同樣的 4 週不背、5 %、10 %、15 %；「The max pack weight we typically recommend for aerobic base training hikes tops out at 25% of body weight」；「We do not recommend doing your heavy pack carries on your longer hikes」。
- ME 頁：「Progress either the weight or the total vertical in a stairstep fashion, but not both at the same time」。
- 注意：`back-to-back-and-long-day.md` 寫的是「最多 25 %」，那是 mountaineering 頁；trekking 頁現在寫的是 20 % 或行程重量。這份文件採 trekking 頁，因為百岳比較接近健行。

**其他來源：**

- CTS（Jim Rutberg，2025-03-07，**教練經驗**）：負重健行「Aim for 10% of bodyweight」；「Once vests get above 20% of bodyweight …, the sport specific benefits typically decrease」；建議背「the pack you intend to use in your event」。
- Orr 2021：強度可以靠「speed of march, grade, and type of terrain」來調，不必只加重量。
- PTT 網友（筆記轉述，**未驗證**）：「維持訓練頻率比衝一次量然後大休N天有效的多」。

**套到範例跑者（70 kg，行程 9 kg；推估）：**

| 賽前週 | 階段 | 背包 | 長天時間 | 機器課 |
|---|---|---|---|---|
| 14–11 | 基礎後期 | 0（ME 背心 0 → 10 %） | app 的 `long_min` | — |
| 10–9 | 專項 1 | 5 % ≈ 3.5 kg | 新重量的第一次：`long_min` × 0.75，最多 3 小時 | 40–50 分，3.5 kg |
| 8–7 | 專項 2 | 10 % ≈ 7 kg | 同上規則 | 40–50 分，7 kg |
| 6–3 | 專項 3 | 行程背包 9 kg（13 %） | 第二次起回到 `long_min`；B2B 第 1 天背 9 kg | 40–50 分，9 kg；最後兩次可以到 1.15 × 9 ≈ 10.5 kg |
| 2–1 | 減量 | 賽前 14–8 天：一次 30 分、9 kg；之後不背 | — | — |

- **一次只加一樣**（UA）：換新重量的那一次，時間縮短；同一個重量做過一次，才把時間或爬升加回去。
- **背包長天的長度**：UA 說不要在最長的那天背重。範例跑者的行程背包才 13 %，所以專項 3 可以背著做 `long_min`。新重量的第一次用 0.75 倍、3 小時上限（**推估**）。
- **最重的上限**：
  - 背包長天：行程背包（`pack_kg`）。
  - 機器短課：min(1.15 × 行程背包, 20 % 體重)。1.15 是**推估**；20 % 是 UA trekking 和 CTS 的上限。
  - 行程背包如果 < 5 % 體重，跳過 5 % 那一步。
- **跟團的影響（推估）**：跟團的速度比你自己走慢，心肺不是瓶頸。瓶頸是第 2、3 天下坡的腿和腳。所以專項 3 的重點是「背著下坡」，不是「背更重上坡」。
- **背包本身**：用行程要用的那一個（CTS）。要確認 9 kg 落在那個背包標示的舒適負重內；訓練時用行程那一個，才練得到肩帶和腰帶的磨合。

### 2.4 和 B2B 怎麼配

`back-to-back-and-long-day.md` §2.4–2.5 已經決定：B2B 只在專項期，每個 3:1 週期最多 1 次；第一次 B2B 第 1 天背約 5 kg，第二次背 9 kg，第 2 天比第 1 天輕或一樣。這份文件只補三件事：

1. **B2B 算 1 次背包長天，第 2 天也算 1 次負重課**（如果有背）。所以 B2B 那個 28 天裡，只剩 2 次可以給機器課。
2. **B2B 週不排平日機器背包課**（推估）。B2B 前一週稍輕（Johnston），B2B 後 3–4 天輕鬆；Blacker 2010 的 72 小時恢復也落在這段。
3. **重量進度和 B2B 對齊**：第一次 B2B 落在專項 1 或 2（5–7 kg），第二次落在專項 3（9 kg），正好就是 §2.3 的表。3 天版本（賽前 4–6 週）用 9 kg，第 2、3 天的背包和第 1 天一樣（多日行程每天只少吃掉的食物，`capacity.PACK_DAILY_DROP` = 0.7 kg/天）。

### 2.5 減量

- 背包長天：最後一次在**賽前 ≥ 14 天**，也就是專項期的最後一個週末（推估；UA mountaineering 1 週減量、量減半；app 本來就有 14 天的減量期）。
- 賽前第 14–8 天：可以有一次 30 分、行程背包的機器課或短爬坡。這是 UA「maintain the intensity」的版本，心率 ≤ AeT（推估）。
- 賽前 7 天內：不背包、不做 ME。肌力：A 賽事前 14 天內整個不排（SP-86）。肌力週期化筆記轉述《運動訓練法》：「在參加主要比賽之前的5~7天，應終止肌力訓練」，長距離耐力項目「比賽前2周就可停止肌力訓練」——後者已對照原書核對：Bompa & Buzzichelli《Periodization Training for Sports》p.184；減量第 2 週不排肌力，p.327。
- 沒有找到「負重能力停練後多久消失」的研究（Orr 2021 也沒談減量或停訓）。

---

## 3. 課表種類

每一種都寫：處方、強度、背多重。全部的數字，除了標來源的以外，都是**推估**。

### 3.1 背包爬坡健行（週末，山路）

| 項目 | 內容 |
|---|---|
| 處方 | app 現有的長天（`long`），加上背包。選每公里爬升 ≥ 目標 70 % 的路線（`overview.py:643–644`，江晏慶「抓七成」） |
| 強度 | 全程心率 ≤ AeT（UA；`back-to-back-and-long-day.md` §1.3 的 ① 到 ⑤ 照用）。陡坡走路還是超過，照 ③ 另外標，不算失敗 |
| 背包 | §2.3 的表；用行程的那個背包 |
| 下坡 | 專項 1：可以把水倒掉下山（UA 的水壺做法）。專項 2 起：**背著下山**，這才是百岳第 2、3 天的情境（推估） |
| 補給 | 練行程吃的東西 |

### 3.2 平日樓梯機／跑步機坡度背包課（40–50 分）

| 項目 | 內容 |
|---|---|
| 處方 | 暖身 5–10 分不背包 → 30–40 分背包上坡 → 緩和 5 分。跑步機坡度 12–15 %（大多數跑步機的上限），或樓梯機 |
| 強度 | 心率 ≤ AeT（有氧課，不是 ME）。從「到 AeT 的機器設定」開始，背包加重時**降速度、不降坡度**，讓心率留在 AeT |
| 背包 | §2.3 表的「機器課」欄；最後兩次可以到 1.15 × 行程背包，≤ 20 % 體重 |
| 爬升量 | 跑步機：速度 × 坡度 × 時間。例如 3.5 km/h、15 %、35 分 ≈ 300 m。TSS 照 UA 的機器規則把爬升打五折（`uphill-athlete-mountain-metrics.md` §1(b)） |
| 為什麼可以 | UA trekking：「incline treadmills and stair machines are excellent substitutes」；「a sturdy step-up box or the stairs in the nearest tall building」 |
| 反對意見 | Koop（CTS）把爬樓梯、停車場列在「Options to avoid」，只說有其他選擇時「*might* … be worth your time」；他也說背心跑步「a good way to get slow and injured」。他講的是**跑步**和越野跑者，不是負重健行；這份文件只用「走」的機器課，不背包跑步 |
| 缺點 | 沒有下坡；平地不穩定的路面也練不到。所以只能當補充，不能取代週末的山路 |

### 3.3 背包下坡與離心

| 項目 | 內容 | 來源 |
|---|---|---|
| 背著下山 | 專項 2 起，週末長天背著下山（§3.1） | 推估；Blacker 2010（背 25 kg、−8 % 坡後 72 小時恢復）提醒強度不要低估 |
| 箱上前弓步（step-off lunge） | 從箱子往下跨、落地馬上停住再回去；箱子越高衝擊越大（15 cm 約 4 倍體重、30 cm 約 5.5 倍）；先「墊高」，再用負重背心微調 | 山姆教練的前弓步示範（筆記轉述）；倍數引自 Alex Natera 的示範，**未驗證** |
| 分腿蹲練離心 | 「分腿蹲要練習離心的動作，也就是由上往下蹲的動作」；單邊比雙邊好、「要做快速離心的動作」 | 登山肌力心得文（筆記轉述，**未驗證**） |
| 等長（肌腱） | 「後腳抬高式底部等長維持」、「登階等長維持」，每次 30 秒；理由是肌腱微血管少、恢復慢 | 負重爬山族群的膝主導訓練文（筆記轉述，**未驗證**） |
| 下坡跑的重複負荷效應 | Bontemps 2025：10 次下坡跑後痠痛 8.7 vs 29.6 mm | 同儕審查（轉引自 `vo2max-gate-and-trail-metric.md`，沒有重新核對） |

### 3.4 肌力與肌耐力（ME）

**UA／Evoke 的具體課表（教練經驗）：**

| 來源 | 內容 |
|---|---|
| **Scott Johnston（Evoke，2022-10-06）〈Muscular Endurance: All You Need to Know〉** | 健身房：Split Jump Squat、Squat Jumps、Box Step Ups、Front Lunge，每個 10 下、共 6 組，約每秒 1 下。進度（8–14 週，每週至少 1 次）：第 1–2 次不負重、組間休 60 秒；第 3 次休 45 秒；第 4–8 次加 10 % 體重的背心、休息逐步縮短；第 9–14 次 15 % 體重、最後休 10–15 秒。戶外：重量「heavy enough that local fatigue in your legs is the limitation, NOT your breathing」，坡度「typically 30% grade or more」，「no more than one hour of total climbing time」，新手 30 分。「You MUST maintain the Z1-2 aerobic volume and add this training on top of that」 |
| **UA（2016-11-27）ME 頁** | 戶外負重爬坡：新手「Start with 5 to 10 percent of body weight」，有經驗的人 10–30 %；坡度 30–100 %；「6 to 10 workouts spanning roughly six to eight weeks」；用水壺裝水，到頂倒掉；感覺是「carry on a conversation while your legs are burning」 |
| **UA（Martin Zhor、Ben Morley，2026-05-19）〈Why Your Legs Fail〉** | 健身房：box step-ups（上坡、向心）、box step-downs（下坡、離心）、walking lunges；進度靠縮短休息，不是加重；8–16 週、每週一次 |
| **前提（UA、Johnston）** | AeT 要在 AnT 的 10 % 以內（「within 10 percent of your AnT as measured by heart rate」）。app 已經有這個判斷：`quality_gate.ua_gap`、`UA_GAP_MAX = 0.10`（`quality_gate.py:73, 191`） |

**筆記轉述（未驗證）：**

- 肌耐力的重量和次數：「20%~50%RM 肌耐力」；「要練到長時間的肌耐力，要能做50~100+次數的重量」。
- 肌耐力課表：每個動作兩組、每組 20 下；「從肌力訓練轉換到肌耐力訓練」（登山肌力心得文）。
- 上坡「主要需要肌力，次要有氧能力」，可以「把有氧跟肌力拆開來練」（PTT 網友）。

**建議：**

- ME 健身房版：基礎後期起，把 app 的一次 `strength` 課換成 Johnston 的進度（背心 0 → 10 → 15 % 體重）。
  - 前提：`ua_gap ≤ 10 %`。沒有實測 AeT 或差距 > 10 % 時，維持一般肌力課。
- 戶外 ME 負重爬坡（≥ 30 % 坡、腿先酸）：**不預設排**。
  - 原因：一週只排一次質量課時，專項期的質量課已經是「爬坡間歇 5×4 分」（`overview.py:665`）。ME 之後又要 3 天輕鬆（UA），排不進去。
  - 給一個偏好讓使用者選：用 ME 取代基礎後期的質量課（推估）。
- 強度：ME 用感覺（能講話、腿在燒）；心率可以超過 AeT，不套 `back-to-back` 的 ①（Keena：「a little bit of volume above AeT is OK」，轉引自 `back-to-back-and-long-day.md` §1.1）。

### 3.5 徐國峰

- 沒有找到徐國峰談負重或背包訓練的文章或筆記（網路搜尋「徐國峰 負重 爬山 訓練 背包 百岳」沒有相關結果）。
- 他的「有氧基礎足夠才加強度」（`quality_gate` 的 xu 系列）可以當 ME 的另一個前提，但這是**推估**的延伸。

---

## 4. 小結：一張表

| 課 | 何時 | 頻率 | 強度 | 背包 |
|---|---|---|---|---|
| 背包長天（山路） | 專項期 | 每 10–14 天最多 1 次（含 B2B 第 1 天） | ≤ AeT | 5 % → 10 % → 行程背包 |
| 機器背包課 40–50 分 | 專項期 | 和背包長天隔週輪流；B2B 週不排 | ≤ AeT | 同階段；最後兩次 ≤ 1.15 × 行程、≤ 20 % 體重 |
| ME 健身房 | 基礎後期 → 賽前 3 週 | 取代 1 次肌力課，每週 1 次 | 感覺（腿限、能講話） | 背心 0 → 10 → 15 % 體重 |
| 戶外 ME 負重爬坡 | 基礎後期（偏好） | 取代質量課，每週 1 次 | 感覺 | 5–10 % 起 |
| 下坡離心（肌力課裡） | 全年，專項期加重 | 肌力課裡 | — | 墊高優先，背心微調 |
| 總量 | — | 負重課每 28 天 ≤ 4 次 | — | 不超過 20 % 體重 |

---

## 5. app 設計（給實作的人照著做；這次沒有改程式）

### 5.1 背包重量欄位

| 用途 | 欄位 | 現況 | 建議 |
|---|---|---|---|
| 行程背包 | `Event.pack_kg: Optional[float]` | **沒有**。`Event` 只有 `days`、`kind` 等（`planning.py:56–89`）；背包只在預測頁的 `PredictIn.pack_kg`、`PlanIn.pack_kg_by_day`（`api/racepower.py:188, 674`），沒有存回事件 | 新增。0–40 kg（和 `set_hike_meta` 的檢查一樣）。沒填：`days ≥ 2` 用 `capacity.PACK_DEFAULT_MULTI`，否則 `PACK_DEFAULT_SINGLE`（都是 9 kg，`capacity.py:45–46`）。多日用第 1 天的重量（最重） |
| 體重 | 有效體重 | `plan.weights` → 沒有時用 WKO5（`api/plan.py:200–220`） | 照用。r = pack_kg ÷ 體重 |
| 每次訓練背多少 | `racepower_hike_meta.json` 的 `pack_kg`（`athlete.set_hike_meta`，`athlete.py:1331–1355`） | 已經有，以 activity 檔名為 key，現在給百岳用 | 照用，但要開放給**所有**運動類型（跑步機坡度課、越野）。訓練頁在計畫裡是負重課的那一次，自動帶入計畫重量，讓使用者改 |
| 計畫裡的重量 | `Session.pack_kg: Optional[float]` | 沒有 | 新增到 `overview.Session`（`overview.py:315–329`） |

### 5.2 什麼時候排

```text
target = 下一個 A 事件（B 事件看偏好），而且 kind == "baiyue" 或 days > 1
loaded_on = target and prefs.loaded != "off"       # 新偏好 plan.prefs.loaded：auto / off（受傷史時關掉）
w = 距 target.start 的週數（向下取整）
L_trip = target.pack_kg or PACK_DEFAULT_MULTI
W = 有效體重

step(w):
    w ≥ 15         → None                       # 不背包
    11 ≤ w ≤ 14    → "me"                        # 只有 ME 健身房版
    9 ≤ w ≤ 10     → min(0.05·W, L_trip)
    7 ≤ w ≤ 8      → min(0.10·W, L_trip)
    3 ≤ w ≤ 6      → L_trip
    w ≤ 2          → "taper"
```

- `w` 對應 app 的訓練期：專項期 = 賽前第 10–3 週（`planning.py:30–31, 240–244`）。如果使用者手動改了訓練期，用**訓練期**而不是 `w`：
  `specific` 的前 1/4 → 5 %，第 2 個 1/4 → 10 %，後 1/2 → `L_trip`（推估）。
- 不排的情況：`mode in ("recovery_week", "reentry")`；`tsb_today < −20`（app 的維持量線，和 B2B 一樣）；近 28 天負重課已經 4 次。
- 重量階段是**以「完成過一次」為準**，不是只看日期：如果上一個階段一次都沒做（例如請假），下一週維持上一個階段，不跳級（UA「stairstep」；推估）。

### 5.3 每週怎麼放

1. **背包長天**：在 `week_plan` 建立 `long` 之後（`overview.py:651–655`）：
   - 條件：`step` 是數字，而且距上一次背包長天 ≥ 10 天（看已完成的課，用 §5.1 的 `pack_kg` ≥ 計畫的 0.8 倍判斷）。
   - `long.pack_kg = step`；`detail` 加上「背 X kg（體重的 Y %），心率 ≤ AeT；專項 2 起背著下山」。
   - 這是新重量的第一次：`minutes = min(long_min × 0.75, 180)`。
   - B2B 週（`b2b_due`）：第 1 天就是背包長天；`long2` 的 `pack_kg` 等於第 1 天（`back-to-back-and-long-day.md` §3.2）。
2. **機器背包課**：沒有排背包長天的那一週，而且不是 B2B 週：
   - 新 session：`id="carry"`、`kind="mountain"`（現有的 kind）、`terrain="hike"`。
   - `minutes = min(cap_weekday or 50, 50)`；`pack_kg` = 同階段，專項 3 的最後兩次 = min(1.15 × L_trip, 0.20 × W)。
   - `detail`：「跑步機 12–15 % 或樓梯機，暖身 5–10 分不背，背包上坡 30–40 分，心率 ≤ AeT；加重時降速度、不降坡度。」
   - 分鐘數從 `easy` 的總量扣（`overview.py:685–694` 的 `left`），**不增加週量**（和 B2B 一樣）。
   - `plan_prefs.place()`：不要排在背包長天或質量課的前一天、隔天（硬課 48 小時規則，`plan_prefs.py:540–546`，把 `carry` 當硬課）。
3. **ME 健身房版**：`step == "me"` 或專項期，而且 `QG.ua_gap(aet, lthr) ≤ QG.UA_GAP_MAX`：
   - 把 `strength1` 換成 `id="me"`、`kind="strength"`、`title="肌耐力（ME）"`。
   - `detail` 依完成次數 n：n ≤ 2：不負重、休 60 秒；n = 3：休 45 秒；4–8：背心 10 % 體重；9–14：15 % 體重、休 10–15 秒（Johnston）。
   - 賽前 3 週起停 ME，換回一般肌力；A 賽事前 14 天內（減量期＋比賽週）不排肌力（§2.5；SP-86 採用 Bompa & Buzzichelli p.184「長距離耐力項目賽前 2 週停肌力」，已在 app 實作）。
4. **減量**：`step == "taper"`，而且距賽前 8–14 天、前一次負重課 ≥ 5 天：一次 `carry`，30 分，`pack_kg = L_trip`。之後都不排。

### 5.4 怎麼判斷做得好不好

**資料：**

- 戶外：`hikehr.filter_windows` 的 100 m 窗（`hikehr.py:54–71`）。
  - **問題**：現在的濾網只收**心率 ≥ AeT** 的窗（`HIKE_FILTER`，給百岳跟團用）。負重訓練課本來就壓在 AeT 以下，大部分窗會被丟掉。
  - **做法**：`filter_windows` 加一個參數 `hr_band=(aet − 15, aet + 3)`，給訓練評估用；百岳原本的用途不變。−15 是**推估**，+3 是 `AET_MARGIN`。
- 機器：沒有 GPS 爬升。用課表上的設定（速度、坡度）算 VAM；使用者在訓練頁輸入或確認設定（推估）。只用後 2/3 的時間（避開心率延遲，τ ≈ 55–70 秒，`vo2max-gate-and-trail-metric.md`）。

**指標：**

| 指標 | 怎麼算 | 依據 |
|---|---|---|
| **ΔHR@VAM（背包 vs 不背包）** | 近 8 週**不背包**的爬坡窗（同一坡度帶 `GRADE_BANDS`）擬合 HR ~ VAM 直線（和 `hikehr.fatigue` 同樣的最小平方法）；背包課的窗殘差中位數（bpm） | `hikehr.fatigue` 的做法（無外部來源，F17）；Simpson 2011 提醒：自選速度時心率可能不變，所以要配下一列 |
| **負重效率 E_L** | 同一坡度帶、心率在帶內：觀察到的 VAM 比例 =（背包 VAM 中位數 ÷ 不背包 VAM 中位數），除以 Pandolf 預測的 `capacity.pack_ratio(g, L, 0, W, e_aet)` | Pandolf 1977、`capacity.py` B3。E_L ≈ 1：和物理預測一樣；> 1：背得比預測好（推估的判讀） |
| **背包長天的後段漂移** | 同一課，最後 1/3 對最前 1/3 的 ΔHR@VAM | `back-to-back-and-long-day.md` §1.3 ④；Maunder 2021（durability） |
| **背包下坡** | 坡度 −10 % 到 −30 % 的移動速度，背包 vs 不背包的比例 | 描述統計；Pandolf 不能用在下坡（`hike.py:36–38`），所以**不做**物理預測 |
| **隔天** | B2B 第 2 天照 `back-to-back-and-long-day.md` §2.6 的 2 × 2 判讀 | 同左 |

**判讀（推估）：**

- 只看**趨勢**：同一個專項期裡，同一個重量階段，第 2 次比第 1 次的 E_L 有沒有上升、ΔHR@VAM 有沒有變小。
- 「有差」的界線：±3 bpm（`AET_MARGIN`）、±5 % VAM（UA 的 5 %），和 B2B 文件一致。
- 背包重量變了，就不和上一個階段比，只和同重量比。Pandolf 本身在不同重量間的誤差約 ±8–15 %（`capacity.SIGMA_PACK` = 0.08，Looney 2022／Weyand 2021），比訓練效果還大。
- 樣本：窗數 < 5（`FAT_MIN_N`）不判讀，只顯示時間和爬升。
- 熱天（Hadley > 150）照算，但附註（B2B 文件 ⑤）。
- **餵回預測模型**：背包課的窗加上 `pack_kg` 後，可以直接進 `capacity.fit_walk_capacity` 的 `hike` 窗（它已經用 `pack_of(trip)`，`capacity.py:561–577`）。
  這樣 `pack_range` 會涵蓋 9 kg，預測時就不用加 `SIGMA_PACK`（`capacity.py:444–445`：超出資料範圍才加）。這是負重課的第二個好處。

### 5.5 TSS

- 計畫的 TSS：`tph["hike"]`（45/h，`overview.py:49`）× 時間，加上 UA 的負重項：
  `load_TSS = 10 × (L/W ÷ 0.10) × gain_ft/1000`，只在 L/W > 10 % 時加（第三方計算機的寫法，`uphill-athlete-mountain-metrics.md` §1(b)，**未驗證**是否為 UA 原意）。
- 機器課的爬升先打五折（UA 論壇，同文件）。

### 5.6 顯示

- 計畫頁的負重課卡片：計畫重量、體重 %、這是本階段第幾次、距下次升級還差幾次。
- 訓練頁：「這次背多少」（預設帶入計畫重量）→ 寫進 `racepower_hike_meta.json`。
- 完成後：ΔHR@VAM、E_L、和上一次同重量比較；可以接到 `coaching-dashboards-mountain.md` 的爬坡圖。

---

## 6. 待決定

1. B 級的多日百岳要不要也觸發負重課，還是只看 A 級？
2. 機器課最後兩次的 1.15 倍超載要不要？（UA 允許到 20 % 體重；CTS 建議 10 %。9 kg 已經 13 %。）
3. 戶外 ME 負重爬坡要不要做成「取代基礎後期質量課」的偏好？
4. `filter_windows` 的訓練用心率帶要用 AeT − 15 到 AeT + 3，還是用 `hikehr` 已有的 HR 分帶？
5. `Event.pack_kg` 要不要在預測頁存檔時自動寫回事件？

---

## 7. 參考資料

### 同儕審查（只讀了摘要；Orr 2021 讀了部分全文）

- Pandolf KB, Givoni B, Goldman RF. Predicting energy expenditure with loads while standing or walking very slowly. *J Appl Physiol* 1977;43:577–581. DOI 10.1152/jappl.1977.43.4.577. PMID 908672.
- Knapik JJ, Harman EA, Steelman RA, Graham BS. A systematic review of the effects of physical training on load carriage performance. *J Strength Cond Res* 2012;26:585–597. DOI 10.1519/jsc.0b013e3182429853. PMID 22130400.（Orr 2021 的參考文獻列的 DOI 是 10.1519/JSC.0b013e31820f202d，和 Europe PMC 不同；這裡用 Europe PMC 的）
- Rudzki SJ. Weight-load marching as a method of conditioning Australian Army recruits. *Mil Med* 1989;154:201–205. DOI 10.1093/milmed/154.4.201. PMID 2499831.
- Wills JA, Saxby DJ, Glassbrook DJ, Doyle TLA. Load-carriage conditioning elicits task-specific physical and psychophysical improvements in males. *J Strength Cond Res* 2019;33:2338–2343. DOI 10.1519/jsc.0000000000003243. PMID 31269002.
- Wills JA, Saxby DJ, Glassbrook DJ, Doyle TLA. Sex-specific physical performance adaptive responses are elicited after 10 weeks of load carriage conditioning. *Mil Med* 2023;188:658–664. DOI 10.1093/milmed/usab470. PMID 34791364.
- Orr RM, Dawes JJ, Lockie RG, Godeassi DP. The relationship between lower-body strength and power, and load carriage tasks: a critical review. *Int J Exerc Sci* 2019;12:1001–1022. DOI 10.70252/ovvg8388. PMC6719820.
- Orr R, Pope R, Lopes TJA, Leyk D, Blacker S, Bustillo-Aguirre BS, Knapik JJ. Soldier load carriage, injuries, rehabilitation and physical conditioning: an international approach. *IJERPH* 2021;18:4010. DOI 10.3390/ijerph18084010. PMC8069713.
- Orr RM, Pope R, Johnston V, Coyle J. Soldier occupational load carriage: a narrative review of associated injuries. *Int J Inj Contr Saf Promot* 2014;21:388–396. DOI 10.1080/17457300.2013.833944. PMID 24028439.
- Orr RM, Johnston V, Coyle J, Pope R. Reported load carriage injuries of the Australian army soldier. *J Occup Rehabil* 2015;25:316–322. DOI 10.1007/s10926-014-9540-7. PMID 25178432.
- Orr RM, Pope RR. Load carriage: an integrated risk management approach. *J Strength Cond Res* 2015;29 Suppl 11:S119–128. DOI 10.1519/jsc.0000000000001029. PMID 26506174.
- Knapik JJ, Reynolds KL, Harman E. Soldier load carriage: historical, physiological, biomechanical, and medical aspects. *Mil Med* 2004;169:45–56. DOI 10.7205/milmed.169.1.45. PMID 14964502.
- Schuh-Renner A, Grier TL, Canham-Chervak M, et al. Risk factors for injury associated with low, moderate, and high mileage road marching in a U.S. Army infantry brigade. *J Sci Med Sport* 2017;20 Suppl 4:S28–S33. DOI 10.1016/j.jsams.2017.07.027. PMID 28986087.
- Kelly K, Niederberger B, Givens A, Bernards J, Orr R. Profiling injuries sustained following implementation of a progressive load carriage program in United States Marine Corps recruit training. *Work* 2024;77:1391–1399. DOI 10.3233/wor-230569. PMID 38552130.
- Stoltzfus KB, Arvanitakis AV, Kennedy LM, McGregor KR, Zhang B, Hu J. Factors associated with musculoskeletal injuries while hiking with a backpack at Philmont Scout Ranch. *Wilderness Environ Med* 2022;33:59–65. DOI 10.1016/j.wem.2021.11.010. PMID 35067448.
- Simpson KM, Munro BJ, Steele JR. Effect of load mass on posture, heart rate and subjective responses of recreational female hikers to prolonged load carriage. *Appl Ergon* 2011;42:403–410. DOI 10.1016/j.apergo.2010.08.018. PMID 20870217.
- Blacker SD, Fallowfield JL, Bilzon JL, Willems ME. Neuromuscular function following prolonged load carriage on level and downhill gradients. *Aviat Space Environ Med* 2010;81:745–753. DOI 10.3357/asem.2659.2010. PMID 20681234.
- Straight CR, McKenzie KL, Sargent AL, et al. Repeated bouts of load carriage alter indirect markers of exercise-induced muscle damage, liver enzymes, and oxygen-carrying capacity in male soldiers. *Physiol Rep* 2025;13:e70268. DOI 10.14814/phy2.70268. PMC12309849.
- Xu C, Silder A, Zhang J, Reifman J, Unnikrishnan G. A cross-sectional study of the effects of load carriage on running characteristics and tibial mechanical stress: implications for stress-fracture injuries in women. *BMC Musculoskelet Disord* 2017;18:125. DOI 10.1186/s12891-017-1481-9. PMC5363036.
- Lee J, Yoon YJ, Shin CS. The effect of backpack load carriage on the kinetics and kinematics of ankle and knee joints during uphill walking. *J Appl Biomech* 2017;33:397–405. DOI 10.1123/jab.2016-0221. PMID 28530482.
- 轉引自其他研究文件、這次沒有重新核對：Ludlow & Weyand 2017、Looney 2022（PMC8919998）、Weyand 2021、Robinson 2018（`baiyue-from-running.md`）；Bontemps 2025（DOI 10.1002/ejsc.12240）、Vernillo 2017（`vo2max-gate-and-trail-metric.md`）；Maunder 2021（`back-to-back-and-long-day.md`）。
- 只在 Orr 2021 裡看到、沒有讀原文：Hauschild V, Roy T, Grier T, Schuh A, Jones B. *Foot Marching, Load Carriage, and Injury Risk*. Army Public Health Center, Technical Information Paper No. 12-054-0616, 2016；Rudzki S. Overuse injuries: how to manage the athlete. *Aust Fam Physician* 1997;26:1185–1189。

### 教練經驗、廠商

- Steve House（UA）— Training for Trekking and Hiking（2025-05-20）：https://uphillathlete.com/trekking/training-for-trekking-and-hiking/
- Steve House（UA）— Training for Mountaineering（2021-03-14）：https://uphillathlete.com/mountaineering/training-for-mountaineering/
- Uphill Athlete — Muscular Endurance Training for Mountain Athletes（2016-11-27）：https://uphillathlete.com/aerobic-training/vertical-beast-mode-what-is-muscular-endurance-why-it-is-important-for-any-alpinist-or-mountaineer-and-how-do-you-train-it/
- Martin Zhor、Ben Morley（UA）— Muscular Endurance for Mountain Athletes: Why Your Legs Fail（2026-05-19）：https://uphillathlete.com/strength-training/muscular-endurance-for-mountain-athletes/
- Uphill Athlete — Exercises for Muscular Endurance（2023-04-27）：https://uphillathlete.com/aerobic-training/exercises-for-muscular-endurance/
- Scott Johnston（Evoke Endurance）— Muscular Endurance: All You Need to Know（2022-10-06）：https://evokeendurance.com/resources/muscular-endurance-all-you-need-to-know/
- Jim Rutberg（CTS）— Weight Vests for Ultramarathon Training（2025-03-07）：https://trainright.com/weight-vest-ultramarathon-training/
- Jason Koop（CTS）— Ultrarunners: How to Train for Mountains When You Live in a Flat Area（2025-03-07）：https://trainright.com/train-for-mountainous-ultramarathon-live-in-flat-area/
- 只有搜尋摘要、沒有讀原文（**未驗證**）：Koop／CTS〈Should Runners Train with Weighted Vests?〉https://trainright.com/should-runners-train-with-weighted-vests/；台灣百岳訓練文章（搜尋摘要提到「背 10 kg、每週 7–8 小時」「最後達到實際重量的 75 %」，出處沒有核對，這份文件沒有採用）。

### 筆記轉述（未核對原出處）

- 登山肌力心得文（單邊、快速離心；下坡膝痛與疲勞、補給；肌耐力課表 2×20；肌力 → 肌耐力）
- 負重爬山族群的膝主導訓練文（改練等長；30 秒）
- 山姆教練的前弓步示範（箱上前弓步、高度與衝擊倍數、墊高＋背心）
- PTT〈單攻的室內訓練請益〉（有氧和肌力拆開練；頻率比單次量重要）
- 肌力週期化筆記，轉述《運動訓練法》（越野跑是長時肌耐力；賽前 2 週／5–7 天停肌力）
- 不同 %RM 對肌力、肌耐力的發展（20–50 %RM、50–100+ 次）
- 越野跑週期化訓練（江晏慶等；強化期別忘了肌力；一週 2 次）
