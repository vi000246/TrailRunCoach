# 「FTP 佔 VO2max 80 %」規則的出處，以及越野日該看心率還是功率

- 日期：2026-10-01
- 分支：`docs/vo2-trail-metric`（只寫文件，不改程式）
- 相關文件：`aerobic-base-readiness.md`、`drift-algorithm.md`、`racepower-v2.md`、
  `interval-prescription.md`、`coaching-dashboards-mountain.md`、`uphill-athlete-mountain-metrics.md`、
  `backend/engine/quality_gate.py`、`backend/engine/base_check.py`、`backend/engine/zones.py`
- 標記：**同儕審查**、**教練經驗**、**廠商**、**推估**（沒有來源的數字或做法）、**未驗證**（查不到原文或沒能核對）。
  徐國峰算來源。

---

## 摘要

1. **規則出處：Tim Cusick（WKO5），不是 Palladino。**
   - Cusick 在 WKO5 webinar 講：FTP 佔 VO2max 的比例在受過訓練的選手身上「通常」停在 81～85 %。
     還沒到 81 % 就繼續練廣度（FTP），到了而且兩三週沒進步，才進 VO2max 期。
   - 「> 80 % 就可以開始練 VO2max 間歇」這句是你自己在 WKO5 Season View 圖表上加的註解。
     那張圖是 WKO5 內建的「Compare VO2max to mFTP as % - Run」，說明欄寫「Contributions from Steve Palladino」。
     所以你會記成 Palladino：圖是 Palladino 提供給 WKO5 的，門檻是 Cusick 講的。
   - 類別：**教練經驗**。原本是自行車的說法，沒有同儕審查。
2. **換到跑步（Stryd）這個比例幾乎沒有鑑別力。**
   - WKO5 的公式在跑步上**結構性地偏高**。你現在的值依算法落在 **83～95 %**；
     過去 16 個月裡只有 2026 年 6～8 月低於 80 %，原因是模型的 FRC 暫時變大，跟有氧基礎無關。
   - **建議不要拿它當 Z5 解鎖條件**。最多當「FTP 停滯」的參考訊號（§1.6）。
3. **越野日的指標：依課表類型決定，不是一律用功率。**
   - 你近 60 趟越野跑，只有 **8.8 %** 的移動時間落在 Stryd 有驗證的 3～8 % 上坡。
     陡坡（> 15 %）佔 26 %，陡下坡（< −10 %）佔 32 %。
   - 長天和連續兩天：心率（AeT 上限）為主。
   - 爬坡重複和可以跑的長爬坡：功率為主，但只在 3～8 % 坡有效。
   - 陡坡健行：心率加 VAM。
   - 下坡：不看功率也不看心率，看下降量和技術。
   - 現在 `zones.py` 的「山路長天 / 越野輕鬆」是功率優先，建議改成心率優先（§2.5）。

---

## 1. 「FTP 佔 VO2max > 80 %」規則

### 1.1 出處

| 候選 | 有沒有這條規則 | 證據 |
|---|---|---|
| **Tim Cusick（WKO5）** | **有，是這條規則的來源** | 見下面引文。**教練經驗** |
| Steve Palladino | 沒有門檻；只提供圖表 | WKO5 Run View 的圖表說明「Contributions from Steve Palladino」。他的課表筆記沒有這個比例 |
| 你自己 | 加上 80 % 這個數字 | `~/WKO5/Views/Athlete/WKO5 Season View.wko5chart`，同一張圖的 series9 標籤：「當FTP%VO2max >80%,代表可以開始練VO2max強度間歇」。WKO5 內建的 `WKO5 Basic Run View.wko5chart` 有同一張圖，但沒有這句 |
| Coggan | 沒找到這條規則 | 只有 Level 5 VO2max = 106–120 % FTP（你的筆記 `65 ⚡ 功率訓練/功率區間說明與訓練目的 --star.md:122`，出自《自行車功率訓練全書》）。換算下來，FTP 約是 VO2max 功率的 83–94 %，但這是我們的換算（**推估**），Coggan 沒有拿它當門檻 |
| Friel | 沒找到 | — |
| Stryd | 沒找到 | **未驗證**（只做了有限的搜尋） |
| 運動科學文獻 | 沒有「> 80 % 才能練 VO2max」的門檻；只有描述性的數字 | §1.3 |

**Cusick 原話**（你的筆記，WKO5 webinar「Building FTP, TTE, and Stamina with WKO5」，
`300 Sport/70 ⏳ 周期化訓練/研討會Building FTP, TTE, and Stamina with WKO5.md`）：

- :598–599 「你可以把 FTP 想成一棟『有氧房子』裡的一個高度。這棟房子的天花板就是你的 VO2 max。」
- :614–617 「如果你已經太高，快要頂到天花板了……你需要進入第三階段，提升 VO2 max，也就是『把天花板升高』。」
- :625–626 「紅線代表 VO2 max，紫色線代表 FTP 占 VO2 max 的百分比。」
- :635–638 「當這兩條線在某一階段變得平坦超過兩三週……那就是轉換階段的時機點。」
- :640–644 「對受過訓練的選手來說，這個平坦區通常出現在 81～85% 之間。所以如果某選手的 FTP/VO2 max 比例還沒到 81%，就不要急著進入下一階段，繼續做廣度訓練。」
- :655–662 用自己過去最好的比例當目標。
- :679–680 「理想值一般介於 81～85%。不太可能再高於 85%。」

其他場次的同一個觀念：

- `65 ⚡ 功率訓練/研討會整理/研討會 The Power of Performance.md:404–405`：「訓練良好的運動員通常 mFTP 能達到 VO2max 的 85% 左右。」
- `…/研討會 Individualizing Your Training Part 2.md:570–571`：追蹤「VO2max 功率對 FTP 的比例，也就是 MAP（最大有氧功率）對 FTP 的平衡」。
- `…/研討會 The Art of Coaching with Data.md:770, 781–782`：同樣看「VO2max 功率與 mFTP 的比率」，但「我不會把 ratio 當作**目標值**，而是**參考指標**」。

網路上的二手轉述也一樣（intervals.icu 論壇）：

- 「when you reach an FTP of about 85% of VO2Max … you need to then start to raise your ceiling and do some VO2Max work」
- 「80-84% is max you can get」
- https://forum.intervals.icu/t/percentage-of-ftp-to-vo2max/13343
- 論壇轉述，**未驗證**到原 podcast。

**這條規則原本的意思和你記的不同：**

- Cusick 講的是週期**什麼時候換階段**：FTP 練到快頂天花板而且停滯了，就換去練 VO2max，目的是再把 FTP 拉上去。
- 他沒有講「什麼時候身體**準備好、可以安全地**練 VO2max」。
- 有氧基礎門檻問的是後面這個問題：有氧基礎能不能承受 5 區間歇（台灣教練）。
- 兩個問題不一樣。

Palladino 的筆記也沒有這條規則。他練 VO2max 的條件是：

- 階段：基礎期之後才進 MAP。
- 強度用 CP 的百分比：MAP 間歇是「FTP/CP 的 103–105%；更個別化的處方則是 103–109% 或更高」
  （`65 ⚡ 功率訓練/功率區間說明與訓練目的 --star.md:133–134`）。
- 每趟「一般在 2.5–3 分鐘」（同檔 :139）。

### 1.2 確切定義：WKO5 怎麼算

WKO5 Run View 的「Compare VO2max to mFTP as % - Run」有三條線
（從 `.wko5chart` 讀出來的運算式）：

```
mFTP           = ftp(meanmax(runpower),90)
VO2max (L/min) = vo2max(meanmax(runpower),90)
mFTP % VO2max  = (avg(metric(lookup(weight,now)))*0.007 + 0.0108*ftp(meanmax(runpower),90))
                 / vo2max(meanmax(runpower),90)
```

- **分子**是 ACSM 的**腳踏車**攝氧公式：VO2 (L/min) ≈ 0.0108 × W + 0.007 × kg。
  也就是「在 mFTP 時的攝氧量」。跑步的視圖也用這條腳踏車公式。
- **分母**是 PD 模型的 VO2max。WKO5 反組譯的結果是
  `VO2max = (FRC_J / 589 + mFTP) / 84.5 + 0.656`
  （`docs/wko5-internals/formulas.md` §6.7；`backend/engine/algorithms/wko5_pdmodel.py:164`）。
- 所以 WKO5 的「VO2max 功率」其實是 **mFTP + FRC/589**。它不是 5 分鐘的 MMP，也不是 ramp 測驗的 MAP，
  是從 PD 模型的 mFTP 和 FRC 推出來的。
- 比例本身是**攝氧量**的比例（在 mFTP 的攝氧量 ÷ VO2max），不是功率的比例。

**「VO2max 功率」的其他算法：**

| 定義 | 典型的 FTP／它 | 來源 |
|---|---|---|
| WKO5 PD 模型（上面） | 自行車 81–85 % | Cusick（**教練經驗**） |
| 5 分鐘 MMP（intervals.icu 論壇的「5min PPO」） | 和 WKO5 接近 | 論壇（**未驗證**） |
| Ramp 測驗的 MAP／峰值功率 | 約 72–80 % | 部落格轉述 Joyner & Coyle 2008 與 Lucía 2002（**未驗證**，原文沒有這組數字，見 §1.3） |

結論：門檻數字跟著定義走。**同一位選手，用 ramp MAP 可能算出 75 %，用 5 分鐘 MMP 可能算出 88 %。**
沒有說清楚分母是什麼的「80 %」，沒有意義。

### 1.3 文獻：乳酸閾值佔 VO2max 的比例（fractional utilisation）

- **Joyner & Coyle 2008**，*J Physiol* 586:35–44，DOI 10.1113/jphysiol.2007.143834（全文，PMC2375555）。**同儕審查（綜論）**：
  - 「in untrained subjects there is typically no sustained rise in blood lactate concentration until about 60% of VO₂max is reached」
  - 「In trained subjects this value can be 75–90% of VO₂max」
  - 馬拉松大約跑在 75–85 % VO2max，10 km 跑在 90–100 %。
- **Bassett & Howley 2000**，*Med Sci Sports Exerc* 32:70–84，DOI 10.1097/00005768-200001000-00012（摘要）。**同儕審查**：
  - 「Running economy and fractional utilization of VO2max also affect endurance performance」
  - 「The speed at lactate threshold … is the best physiological predictor of distance running performance」

**解讀：**

- 文獻說的是「受過訓練的人，閾值落在 VO2max 的 75–90 %」。這是**描述**，不是「到了才能練 VO2max」的門檻。
- 文獻裡沒有任何「比例 > 80 % 才開始 VO2max 間歇」的研究。
- 這些是**攝氧量**的百分比，換成**功率**百分比要另外換算。

### 1.4 為什麼這個比例在跑步上沒有鑑別力

1. **跑者的 W′／FRC 很小，所以 mFTP 和 VO2max 功率貼得很近。**
   - WKO5 的 VO2max 功率 = mFTP + FRC/589。你的 FRC 約 5–6 kJ，所以只多 9–10 W。
   - 換成直接的功率比 mFTP ÷ (mFTP + FRC/589)，你一直在 89–96 %。
   - 圖上那條攝氧量比例會低一些，因為分子分母都有 0.007 × 體重和 0.656 的常數。
   - 但它主要還是在讀 **FRC／FTP**，不是在讀有氧基礎。
2. **Palladino 自己的處方也是這樣。**
   - MAP 間歇 = 103–109 % CP（`功率區間說明與訓練目的 --star.md:133–134`）。
   - 反推回去，跑者的 CP 約是 VO2max 功率的 92–97 %（我們的換算，**推估**）。
   - 照這樣看，跑者幾乎永遠「> 80 %」。
3. **你的筆記也提到跑者和車手不同。**
   - 「跑者能夠達到 VO2max 的功率-時間組合區域，比自行車選手小得多」
     （`60 🏃 有氧訓練/如何進入VO2max.md:33`）。
   - 也就是 CP 到 VO2max 功率之間的距離比較窄。
4. **分子用的是腳踏車效率。**
   - ACSM 的 0.0108 L/min/W 是腳踏車的係數，Stryd 跑步功率的代謝成本不一樣。
   - 拿 Gravina-Cognetti 2025 平路的數字算：242 W → 實測 3.23 L/min，腳踏車公式給約 3.10 L/min，少了約 4 %。
     （我們用該研究的平均值算的，**推估**；體重假設 70 kg。）
   - 誤差不大，但這是巧合，不是設計出來的。

### 1.5 你現在的值（唯讀計算）

- **方法**：
  - 資料集 `backend.api.wko5views._dataset(source="coros")`，資料庫只讀。
  - 用 app 的 PD 模型移植版 `racepower.athlete.pd_model`：近 90 天有功率的跑步 mean-max，加上同步的 FIT 檔。
  - 從模型參數算三種比例。體重 66.3 kg（資料集的 `weight`）。
  - 腳本在 scratchpad，沒有進 repo。
  - A = WKO5 圖表公式；B = mFTP ÷ (mFTP + FRC/589)；C = mFTP ÷ 近 90 天的 5 分鐘 MMP。

| 日期（90 天窗） | mFTP W | FRC kJ | VO2max L/min | WKO5 VO2max 功率 W | 5 分 MMP W | **A：WKO5 圖** | B：功率比 | C：mFTP / 5 分 MMP |
|---|---|---|---|---|---|---|---|---|
| 2025-07-28 | 191.5 | 7.0 | 3.06 | 203.3 | 202.8 | 82.7 % | 94.2 % | 94.5 % |
| 2025-10-28 | 199.7 | 4.7 | 3.11 | 207.7 | 213.4 | 84.2 % | 96.1 % | 93.6 % |
| 2025-12-28 | 202.9 | 5.2 | 3.16 | 211.6 | 217.4 | 84.0 % | 95.9 % | 93.3 % |
| 2026-02-28 | 187.5 | 6.8 | 3.01 | 199.1 | 217.4 | 82.7 % | 94.2 % | 86.3 % |
| 2026-04-28 | 190.8 | 4.6 | 3.01 | 198.7 | 205.6 | 84.0 % | 96.1 % | 92.8 % |
| 2026-06-28 | 176.0 | 11.0 | 2.96 | 194.7 | 194.7 | **79.9 %** | 90.4 % | 90.4 % |
| 2026-08-28 | 162.6 | 11.7 | 2.82 | 182.4 | 188.8 | **78.9 %** | 89.1 % | 86.1 % |
| 2026-09-28 | 175.0 | 6.0 | 2.85 | 185.2 | 199.5 | 82.7 % | 94.5 % | 87.7 % |
| **2026-09-30** | **191.0** | **5.6** | **3.03** | **200.6** | **227.6** | **83.4 %** | **95.2 %** | **83.9 %** |

**用你的閾值換算 C**（分母是同一個 5 分 MMP 227.6 W）：

- CP 204 W → **89.6 %**
- mFTP 195 W → **85.7 %**

5 分 MMP 227.6 W 可能來自 9/30 的 CP 測試，**未逐筆確認**。

**判讀：**

- 照 WKO5 圖表公式（A），你現在是 **83.4 %**，在 Cusick 的 81–85 % 裡面，也高於你寫的 80 %。
- 過去 16 個月，A 只有在 2026 年 6～8 月低於 80 %。
  - 那段時間 FRC 從約 5 kJ 跳到 11 kJ，同時 mFTP 下降。
  - 這是 PD 模型短時間那一端的變化（可能是某次短衝或資料），**不是有氧基礎變差的證據**。
  - 這正好說明 §1.4：在跑步上，A 主要是在讀 FRC。
- A 的上限可以直接算：FRC → 0 時，你這組數字的 A 約 86.6 %（我們用公式代入的，**推估**）。
  所以 A 只能在約 79–87 % 之間動，「> 80 %」幾乎總是成立。

### 1.6 要不要加進 Z5 解鎖？

現在的 Z5 流程（`quality_gate.py`、`base_check.z5_status`）：

- 有氧基礎用徐國峰 90 分鐘法、UA 差距法、Friel 飄移法其中一條確認。
- 先完成 3 區階梯，才開 5 區。

**建議：不加成解鎖條件，也不加成第四條路徑。**

| 理由 | 說明 |
|---|---|
| 問的是不同的事 | Cusick 的比例回答「FTP 還有沒有成長空間」；Z5 門檻回答「有氧基礎扛不扛得住 5 區」（台灣教練）。比例高不代表扛得住 |
| 跑步上沒有鑑別力 | §1.4–§1.5：你幾乎永遠 > 80 %；低於 80 % 時是 FRC 造成的 |
| 定義不唯一 | WKO5 模型、5 分 MMP、ramp MAP 算出的數字差 10 個百分點以上（§1.2） |
| 證據等級 | 自行車教練經驗；Cusick 本人說只當參考，不當目標（`The Art of Coaching with Data.md:781`） |

**可以留的用法：當「FTP 停滯」的參考訊號，只顯示，不擋課表。**

- Cusick 真正的判準是「兩條線平坦超過兩三週」（:635–638）。
- 這和 app 已有的 `plateau` 模式（EF 停滯 ≥ 8 週，徐國峰）是同一個概念，只是看的是 mFTP。
- 建議在 Z5 卡片上加一行參考：
  「mFTP 3 週內變化 < 1 %（Cusick：平坦 2–3 週就是換階段的時機；1 % 是**推估**）」。
  比例本身只顯示，不設 80 % 或 81 % 的門檻。
- 真的要顯示比例的話：
  - 用 **C（mFTP ÷ 近 90 天 5 分 MMP）**。它直接、可解釋，不依賴 WKO5 的 FRC 換算。
  - 標「Cusick（WKO5，教練經驗，自行車）；跑步上的門檻**未驗證**」。
  - 只看趨勢，不看絕對值。

---

## 2. 越野日：看心率、功率、配速、RPE，還是 VAM？

### 2.1 證據

**Stryd 功率在坡上：**

- **van Rassel et al. 2026**，*IJSPP* 21:597–603，DOI 10.1123/ijspp.2025-0382（摘要）。**同儕審查**：
  - 跑步機上 0、2、4、6、8 % 坡，固定 Stryd 功率（RCP 下 10 %）時，心率、通氣、RPE 都沒有差異。
  - Stryd 在陡一點的坡上「slightly underestimated metabolic power」。
  - 結論：**0–8 % 坡、平滑跑步機上，固定 Stryd 功率 ≈ 固定代謝負荷。**
- **Gravina-Cognetti et al. 2025**，*Sports* 13:294，DOI 10.3390/sports13090294（全文，PMC12473670）。**同儕審查**：
  - 15 名男性越野跑者，跑步機 −7、−5、0、+5、+7 % 坡，速度固定在 70 % vVO2max，Stryd。
  - 功率和 VO2 的相關：−7 % ρ = 0.73、−5 % 0.79、0 % 0.81、+5 % 0.84、**+7 % 0.69**。
  - 從 −7 % 到 +7 %，功率 +90.4 %，VO2 只 +74.2 %，兩者不成比例。
  - 作者寫下坡有「progressive uncoupling between mechanical power and metabolic parameters」，原因是離心收縮。
  - 我們用表上的數字算：同樣 1 W，−7 % 坡的攝氧量比平路多約 7 %（**推估**）。
    也就是**下坡時 Stryd 功率低估了代謝負荷**。
- **Stryd 自己**：「in technical terrain and face steep terrain, you can no longer use a single power number」
  （https://help.stryd.com/en/articles/6879554-trail-and-ultra-racing-with-power，**廠商**；`racepower-v2.md` §2.1.4）。
- **Uphill Athlete**：
  - 「Power values change significantly when you shift from running to walking」
  - 陡坡負重健行時「heart rate and ventilatory awareness remain the more practical and reliable tools」
  - **教練經驗**；`coaching-dashboards-mountain.md` §1.1。

**心率：**

- **心率延遲**：一階時間常數 τ ≈ 55–70 s。
  - Hunt 2015、Hunt 2019、Wang & Hunt 2021，**同儕審查**；`drift-algorithm.md` §2。
  - 由一階模型推出：約 3τ（≈ 3 分鐘）才反應到 95 %（1 − e⁻³）。這是我們的推導（**推估**）。
  - 所以 **3 分鐘以內的坡，心率還沒到位坡就結束了**；20 分鐘以上的長坡，心率很準。
- **心血管飄移**：運動 10–20 分鐘後心率在同樣負荷下慢慢上升（Coyle & González-Alonso 2001，**同儕審查**）。
  熱會放大這個效應（`aerobic-base-readiness.md`）。
  長天後段的心率上限會讓你越跑越慢，這正是 AeT 上限想要的保護。
- **Koop**：
  - 「Prescribing trail run training by heart rate should not be the fallback」（熱、海拔、脫水會扭曲心率）。
  - 他偏好 RPE（https://trainright.com/revolutionize-your-run-training/，**教練經驗**；`coaching-dashboards-mountain.md` §1.2）。
- **肌肉疲勞讓心率下降**：
  - UA：「When local muscular fatigue sets in, heart rate actually drops」
  - （https://uphillathlete.com/strength-training/muscular-endurance-for-mountain-athletes/，**教練經驗**）
  - 所以在長天後段和連續第二天，「心率低」不一定代表輕鬆。

**Uphill Athlete／Evoke 的山地訓練原則（教練經驗）：**

- 有氧基礎靠心率：「An athlete must engage in training below AeT—done most effectively in Zone 2—for hundreds of hours」
  （https://uphillathlete.com/trail-running/training-for-trail-running/）。
- 3 區／肌耐力：「the total time in Zone 3 is between 30 and 60 minutes」，
  「executed on a grade similar to what the athlete will encounter during a race」（同頁）。
- 肌耐力（ME）負重爬坡：
  - 判準是感覺：「legs burning, but still able to carry on a conversation」。
  - 一週一次，8–16 週，排在基礎後期（muscular-endurance 頁）。
- 下坡：「the best way to get better at downhilling is to run downhills」（training-for-trail-running 頁）。
- 長賽事：「replace the muscular endurance work with back-to-back long runs」（同頁）。
- 固定心率的基準爬坡：同一段坡用 AeT 心率跑，時間變快就是進步（`coaching-dashboards-mountain.md` §1.1）。

**下坡的離心負荷（同儕審查）：**

- Vernillo et al. 2017，*Sports Med* 47:615–629，DOI 10.1007/s40279-016-0605-y（摘要）：下坡會增加離心負荷、肌肉損傷、神經肌肉疲勞。
- Vernillo et al. 2015，*J Sports Sci* 33:1998–2005，DOI 10.1080/02640414.2015.1022870（摘要）：山地超馬後下坡的能量成本 +13.1 %，作者建議訓練加入下坡。
- Bontemps et al. 2025，*Eur J Sport Sci* 25:e12240，DOI 10.1002/ejsc.12240（摘要）：10 次下坡跑以後，股四頭肌痠痛 8.7 vs 29.6 mm（重複負荷效應）。
- （三篇都轉引自 `interval-prescription.md`，這次沒有重新核對。）

**徐國峰／Daniels（你的筆記）：**

- 徐國峰的 90 分鐘測試用的是心率 1 區的 E 配速（部落格 2016-12）。
- Daniels：T 強度「偏好在平坦的路面上進行……在上下起伏的路面上也可以使用心率錶來監控訓練強度」，
  但要鎖配速的課表心率錶做不到
  （`300 Sport/70 ⏳ 周期化訓練/越野跑周期化訓練(晏慶、K天王、丹尼爾).md:109–111`）。
- 晏慶：越野上下坡轉換課用「體感 8 成力」，不用心率（同檔 :27–29）。

### 2.2 你的越野地形（唯讀計算）

- 範圍：2025-03-15 以後、有功率的越野跑 60 趟，移動時間 82.4 小時。
- 坡度用 `rgrade`。

| 坡度 | 時間佔比 | 30 秒功率 CV（中位數） | Stryd 功率有沒有驗證 |
|---|---|---|---|
| < −10 % | **31.9 %** | 24 % | 沒有；下坡低估代謝（Gravina-Cognetti） |
| −10…−3 % | 10.1 % | 28 % | 只驗證到 −7 %，而且相關較弱 |
| ±3 % | 11.4 % | 29 % | 有 |
| 3…8 % | **8.8 %** | 28 % | **有（van Rassel 0–8 %）** |
| 8…15 % | 11.4 % | 24 % | 外插（**推估**） |
| > 15 % | **26.4 %** | 22 % | 沒有；多半是走路，UA 說改看心率 |

- CV 是同一坡度箱裡所有 30 秒功率的離散程度，會混到不同段，只能粗看。
- **只有約 20 % 的時間在 Stryd 驗證過的坡度（±3 % 加 3–8 %）。**
- 而且每個坡度箱的功率都很不穩，30 秒 CV 22–29 %。
- 你說「越野很難穩功率」，資料支持這個說法：大部分時間是陡上坡（走）和陡下坡，功率不是可靠的強度指標。

### 2.3 原則

1. **課表目標決定主指標。**
   - 目標是「待在 AeT 以下很長時間」：主指標用心率，因為它直接量你要控制的東西，而且長時間下延遲不重要。
   - 目標是「幾分鐘的特定強度」：主指標用功率，因為心率來不及。
2. **功率只在 3–8 % 可以跑的坡上當主指標**（van Rassel；8 % 以上是**推估**）。
   技術下坡和陡坡健行時，功率不當目標。
3. **陡坡（> 12–15 %）用 VAM 加心率**。VAM 是越野上坡最直接的成績；UA 的基準就是「固定心率下的 VAM」。
4. **下坡用劑量控制**（下降公尺、陡下坡時間），強度看 RPE 和技術。心率和功率都會低估下坡的負擔。
5. **RPE 當每次都有的第三個指標**：
   - Koop 把 RPE 排第一，UA 的 ME 也用感覺判斷。
   - 跑後輸入一個 RPE 就夠用。

### 2.4 各課表的處方與評估

AeT、LTHR、CP 都用 app 現行的值。表裡沒有標來源的百分比都是 **推估**。

#### (a) 山路長天 / 越野輕鬆（2–5 小時）

| 項目 | 內容 |
|---|---|
| 目標 | 有氧基礎、時間在腳上、爬升量 |
| **主指標** | **心率：上限 AeT**（UA、徐國峰 1 區） |
| 次指標 | ① 3–8 % 可跑的短坡（< 3 分）：功率上限 88 % CP（Palladino Z2 上緣），避免心率還沒上來就衝過頭。② > 15 % 的坡：用走的，心率仍然 ≤ AeT。③ RPE「可以講整句話」 |
| 不看 | 技術下坡的功率與心率 |
| 範圍 | 心率 ≤ AeT；短暫超過 AeT 不算違規（延遲約 1 分鐘，**推估**容忍 90 秒） |
| **app 怎麼評估** | ① 移動時間中心率 ≤ AeT 的比例，目標 ≥ 85 %（**推估**）② 心率 > AeT + 5 持續 ≥ 3 分的段數（0 = 達標，**推估**）③ **VAM@AeT**：坡度 ≥ 10 %、≥ 15 分、心率在 AeT −10…AeT 的段落，追趨勢（UA 基準爬坡；`uphill-athlete-mountain-metrics.md` §3(a)）④ 時間與爬升照計畫（`compliance.py`）⑤ 不算飄移（`drift_of` 已經排除越野，維持） |

#### (b) 爬坡重複（4–6 × 3–4 分）

| 項目 | 內容 |
|---|---|
| 目標 | 閾值到 VO2max 的刺激、上坡跑的力量 |
| **主指標** | **功率**，選 **3–8 % 可跑的坡**；每趟去掉前 15 秒的平均功率（**推估**） |
| 範圍 | 4 分鐘趟：95–106 % CP（現行 `hill` 列，Palladino 3B–Z4）；5 區版 ≥ 2 分／趟、106–112 % CP（`quality_gate.Z5`） |
| 次指標 | 心率：只看每趟**最後 60 秒**，4 分趟應到 ≥ 95 % LTHR（**推估**，現行列的下限）；RPE 8 左右 |
| 坡 > 8 % | 功率目標是**推估**；改用每趟 VAM 和最後 60 秒心率；同一段坡每次比較 VAM |
| 不看 | 下坡回程的功率；回程就是組休，**走下來**以減少離心負荷（`interval-prescription.md`，**推估**） |
| **app 怎麼評估** | ① 用 `count_reps` 找趟數，算每趟平均功率和坡度 ② 坡度在 3–8 % 才用 `interval_outcome` 判達標／邊界／未適應 ③ 坡度 > 8 % 時標「功率目標推估」，改顯示 VAM 和最後 60 秒心率，同一段坡比歷次 ④ 注意：上坡版的 VO2max 時間比平路少（Gajer 27 % vs 44 %，Buchheit 轉引；`interval-prescription.md`） |

#### (c) 長爬坡閾值（2 × 12–20 分，或一條 20–40 分的坡）

| 項目 | 內容 |
|---|---|
| 目標 | 乳酸閾值、上坡專項肌耐力（UA 3 區 30–60 分，坡度像比賽） |
| **主指標** | 坡度 ≤ 8 % 可以跑：**功率** 90–100 % CP。坡度 > 10–12 % 要走：**心率** |
| 範圍 | 功率 90–100 % CP（Palladino 3A–3B；`interval-prescription` 的 t3c 2×12 @ 90–95 %）；心率 0.95–1.00 LTHR（Friel Z4，同 `threshold` 列），**頭 3 分鐘不看心率** |
| 次指標 | 走的坡：VAM；可以跑的坡：心率（3 分鐘後很準，可以當交叉檢查） |
| **app 怎麼評估** | ① 找出持續 ≥ 10 分、坡度變化小的爬坡段（現有 `climbs` 有 VAM、心率）② 可以跑的段：去掉前 3 分的平均功率在範圍內，加上段內 Pw:HR 前後半差（同坡度才算）③ 要走的段：去掉前 3 分的心率在範圍內，記錄 VAM ④ 同一條坡長期比「同心率下的 VAM」 |

#### (d) 下坡練習

| 項目 | 內容 |
|---|---|
| 目標 | 離心耐受（重複負荷效應）、技術、下坡速度 |
| **主指標** | **劑量**：下降公尺、< −10 % 的時間 |
| 次指標 | RPE 與技術（腳步、控制）；隔天股四頭肌痠痛自評 |
| 範圍 | 第一次少量，之後每週加 10–20 %（**推估**，沒有找到劑量研究）；Bontemps 2025：約 10 次以後痠痛明顯減少 |
| 不看 | **功率**：下坡低估代謝，而且和 VO2 脫鉤（Gravina-Cognetti）；Stryd 自己也說技術地形不能看單一數字。**心率**：下坡偏低，不代表負擔小 |
| 安排 | 放在輕鬆日或長天裡，不排成間歇（`interval-prescription.md`）；不要排在質量課前一天（**推估**） |
| **app 怎麼評估** | ① `downhill_share`（現有）：陡下坡的時間、距離、衝擊（ILR）② 下降公尺和計畫比 ③ 同一段下坡的速度趨勢 ④ 隔天自評痠痛（新欄位） |

#### (e) 連續兩天（週六、週日）

| 項目 | 內容 |
|---|---|
| 目標 | 在疲勞下的耐力（Koop：短時間集中負荷；UA：長賽事用 B2B 取代 ME） |
| **主指標** | **心率**：兩天都是上限 AeT |
| 次指標 | 第 2 天同坡度、同 VAM 下的心率差（`hikehr.fatigue` 已有，`baiyue-from-running.md`）；RPE |
| 注意 | 第 2 天心率**偏低**而 RPE 高，是肌肉疲勞（UA），不是體能變好；這時候要縮短，不要加速 |
| **app 怎麼評估** | ① 兩天各自的 ≤ AeT 比例（同 (a)）② 第 2 天 VAM@AeT ÷ 第 1 天（下降 > 5 % 標「疲勞」，**推估**）③ 第 2 天心率－RPE 不一致（RPE ≥ 6 但平均心率 < AeT − 10，**推估**）標「肌肉疲勞」④ 兩天總負荷照計畫 |

### 2.5 和 app 現在的做法比較

`backend/engine/zones.py:229–262` 現在的做法：

- 「山路長天 / 越野輕鬆」：功率 75–88 % CP 為主，心率 ≤ AeT 當上限。
- 「爬坡重複」：功率 95–106 % CP 為主，心率 95–103 % LTHR 只當參考。
- `TERRAIN_NOTE` 已經寫了心率延遲、Stryd 技術地形的限制、UA 健行看心率、> 8 % 是推估。

| 項目 | 現在 | 建議 | 理由 |
|---|---|---|---|
| 山路長天 | **功率**為主，心率當上限 | 改成**心率（AeT 上限）為主**；功率只當 3–8 % 短坡的上限（88 % CP） | 長天的目標就是 AeT 以下；你只有約 20 % 的越野時間在功率有驗證的坡度；長時間下心率延遲不重要；UA、徐國峰都用心率 |
| 爬坡重複 | 功率為主 | **維持**，但加「選 3–8 % 坡」；> 8 % 改看 VAM 和最後 60 秒心率 | van Rassel 只驗證到 8 %；+7 % 時功率和 VO2 的相關已經降到 0.69 |
| 長爬坡閾值 | 沒有這一列 | **新增**：可以跑的坡用功率 90–100 % CP，要走的坡用心率 0.95–1.00 LTHR（頭 3 分不看） | 長坡的心率在 3 分鐘後就準了；陡坡功率沒有驗證 |
| 下坡練習 | 沒有這一列；`TERRAIN_NOTE` 只說不看功率 | **新增**：用劑量（下降公尺）、RPE、技術；功率和心率都不當目標 | Vernillo、Bontemps、Gravina-Cognetti |
| 連續兩天 | 沒有 | **新增**：兩天都是 AeT 上限，第 2 天看 VAM@AeT 和心率－RPE 是否一致 | Koop、UA；肌肉疲勞讓心率下降 |
| `TERRAIN_NOTE` | 引用 van Rassel、Stryd、UA | 補 **Gravina-Cognetti 的數字**（下坡 ρ 0.73、+7 % 0.69；功率 +90 % vs VO2 +74 %），以及「下坡低估代謝」 | 現在 `drift-algorithm.md:68` 只寫「相關隨坡度下降」，沒有數字；全文已經核對（§2.1） |
| 越野跑的檢討 | 飄移排除越野（對）；沒有越野專屬的達標判斷，只看 `compliance` 的時間和 TSS | 加 §2.4 的評估：≤ AeT 比例、VAM@AeT、坡度內的趟、下坡劑量 | 長天有沒有達到目標，現在沒有判斷 |

### 2.6 一句話建議

- 平日路跑：照舊。
- 週末越野：長天和連續兩天看心率；要拿到特定強度就找 3–8 % 的坡看功率；陡坡看 VAM 加心率；下坡數公尺不看瓦數。

---

## 3. 待使用者決定

1. Z5 卡片要不要加「mFTP 3 週停滯」的參考行？如果要顯示比例，用 C（mFTP ÷ 5 分 MMP），只看趨勢、不設門檻？
2. 「山路長天」要不要從功率優先改成心率優先？
3. 要不要新增「長爬坡閾值」、「下坡練習」、「連續兩天」三列？
4. 跑後 RPE 和隔天痠痛自評要不要做成欄位？連續兩天的「心率－RPE 不一致」和下坡劑量的回饋都需要它。
5. 標成**推估**的數字要不要先上線？包括 ≥ 85 % 在 AeT 以下、下坡每週 +10–20 %、第 2 天 VAM −5 %。上線後再用資料檢查。

## 4. 參考資料

**同儕審查**

- Bassett DR, Howley ET (2000). Limiting factors for maximum oxygen uptake and determinants of endurance performance. *Med Sci Sports Exerc* 32:70–84. DOI 10.1097/00005768-200001000-00012（摘要）
- Joyner MJ, Coyle EF (2008). Endurance exercise performance: the physiology of champions. *J Physiol* 586:35–44. DOI 10.1113/jphysiol.2007.143834（全文 PMC2375555）
- Gravina-Cognetti F, Chaverri D, Planas A, et al. (2025). Mechanical running power and energy expenditure in uphill and downhill running. *Sports* 13:294. DOI 10.3390/sports13090294（全文 PMC12473670）
- van Rassel CR, Gow S, Watanabe T, Jaén-Carrillo D, MacInnis MJ (2026). Validity of Stryd running power for estimating metabolic demand during incline treadmill running. *IJSPP* 21:597–603. DOI 10.1123/ijspp.2025-0382（摘要）
- Hunt 2015／2019、Wang & Hunt 2021：見 `drift-algorithm.md` 參考資料
- Coyle & González-Alonso 2001：見 `aerobic-base-readiness.md`
- Vernillo 2015、2017；Bontemps 2025：見 `interval-prescription.md`（這次沒有重新核對）

**教練經驗／廠商**

- Tim Cusick，WKO5 webinars（你的 notes 筆記，§1.1 列出的行號）
- WKO5 圖表設定：`~/WKO5/Views/Athlete/WKO5 Basic Run View.wko5chart`、`WKO5 Season View.wko5chart`（「Compare VO2max to mFTP as % - Run」）
- Steve Palladino：`300 Sport/65 ⚡ 功率訓練/功率區間說明與訓練目的 --star.md:133–139`
- intervals.icu 論壇：https://forum.intervals.icu/t/percentage-of-ftp-to-vo2max/13343（二手，**未驗證**）
- Uphill Athlete：https://uphillathlete.com/trail-running/training-for-trail-running/ ；https://uphillathlete.com/strength-training/muscular-endurance-for-mountain-athletes/
- Stryd：https://help.stryd.com/en/articles/6879554-trail-and-ultra-racing-with-power
- Koop：見 `coaching-dashboards-mountain.md` §1.2
- 徐國峰：部落格 2016-12 http://rocky549.blogspot.com/2016/12/rq.html；Daniels／晏慶：`越野跑周期化訓練(晏慶、K天王、丹尼爾).md`
