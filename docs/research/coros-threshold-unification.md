# 統一 app 與 COROS 的閾值：LTHR、最大心率（SP-67）

> 調查日期：2026-10-05。只做調查，沒有改程式，沒有對 COROS 發任何請求。
> 標記：**已驗證**＝這次讀到原文、原始碼或資料；**未驗證**＝只看到轉述或搜尋摘要；**推估**＝我的延伸；**待實測**＝要實際操作才知道。
> 來源等級：同儕審查／官方／教練／社群。

## 摘要

1. **推到手錶的課表心率目標不受 COROS 帳號的 LTHR 影響**。app 送的是絕對 bpm（`isIntensityPercent: false`）。讀回的資料顯示 COROS 保留這個 bpm，只把「百分比」欄位用它自己的 LTHR 重算。所以課表目標這一塊，兩邊其實已經一致。
2. 單上說的「COROS 的 LTHR 約 152」是對的，而且不用反推：app 每次同步都直接讀到 COROS 帳號的 `zoneData.lthr = 152`。這個值在 6 月還是 182，現在 152，是 COROS 自己改的。
3. **把 LTHR 寫回 COROS 這條路大概走不通**。COROS 支援文件的說法是 LTHR 只能由演算法自動調整，不能手動輸入（未驗證，支援頁擋了自動讀取）。官方 MCP 和四個社群專案都沒有寫入帳號區間的工具。
4. **不建議反過來用 COROS 的 LTHR**。同儕審查的驗證研究裡，COROS Pace 3 的 LTHR 和實驗室測試的相關只有 r = 0.13，平均誤差約 9 bpm。app 現在刻意忽略它，這個做法正確。
5. 真正會對不上的只有三處：手錶和 COROS app 上顯示的**心率區間**、COROS 的**訓練負荷 TL**（影響 SP-37／38 的換算）、以及課表心率區間選「儲備心率／最大心率」時用的**最大心率**。
6. 建議：app 當唯一的基準，不寫回 COROS。加一張「app 和手錶的數值對照」卡，告訴使用者要到 COROS app 手動改哪幾個數字；TL 換算在 COROS 的 LTHR 變動時要重新起算。

## 1. app 現在的閾值從哪裡來

### 1.1 LTHR

FIT 資料來源（`charts.data_source = source`，本機設定是這個）的順序：

| 順位 | 來源 | 程式 | 標籤 |
|---|---|---|---|
| 1 | 賽季計畫的 dated 列（測試、比賽、手動、套用的估算） | `backend/engine/zones.py:461`（`threshold_row`） | 「你的測試」等 |
| 2 | 從自己的跑步估算（Friel 定義：30 分鐘硬段的最後 20 分） | `backend/engine/wko5expr/fitdataset.py:817`、`backend/engine/thresholds.py:54` | 「自動估算」 |
| 3 | COROS 帳號的 LTHR，**只在完全沒有夠硬的跑步時**當先驗 | `backend/engine/wko5expr/fitdataset.py:757–770`（`_coros_lthr_prior`） | 「來自手錶」 |

- COROS 帳號的 LTHR 平常被放進 `settings_ignored`，不使用（`backend/engine/wko5expr/fitdataset.py:725–755`）。理由寫在程式註解：登入回應沒有說這個值是哪個運動的，而且曾經比同一天 12 分鐘全力測試的峰值心率還高約 10 bpm。
- 本機的 `plan.json` 沒有任何 `lthr` 列，所以 app 的 160 是第 2 順位的**自動估算**（已驗證：本機 `plan.json` 只有一列 `mhr`）。160 這個數字本身我沒有重算，是單上寫的。

### 1.2 最大心率

`backend/engine/hr_profile.py:176`（`max_hr`）：

| 順位 | 來源 | 標籤 |
|---|---|---|
| 1 | 賽季計畫的 `mhr` 列（設定 → 心率） | 「你的設定 日期」或測試方式 |
| 2 | 手錶帳號（COROS `zoneData.maxHr`） | 「來自手錶」 |
| 3 | 近 365 天跑步的估算（`thresholds.estimate_mhr`，`backend/engine/thresholds.py:338`） | 「推估」 |

### 1.3 AeT

`backend/engine/zones.py:465–477`：計畫的 `aethr` 列（實測或套用的估算）→ 自動估算 → 0.89 × LTHR。COROS 沒有 AeT 這個值，所以沒有統一的問題。

### 1.4 四個數字各出自哪裡

| 數字 | 出處 | 驗證 |
|---|---|---|
| LTHR 160 | app 從跑步自動估算（§1.1 第 2 順位） | 來源路徑已驗證；數值沒有重算 |
| LTHR 152 | COROS 帳號 `zoneData.lthr`。本機 `athlete.coros_profile` 存的是 `lthr: 152`（2026-10-04 12:54 UTC）；`athlete_settings` 2026-10-04 那列也是 152 | 已驗證（本機資料庫，唯讀） |
| LTHR 182 | 同一個 COROS 欄位在 2026-05-15 到 06-13 的值（`athlete_settings`） | 已驗證。**COROS 自己把它從 182 改成 152** |
| 最大心率 202 | COROS 帳號 `zoneData.maxHr` 在 2026-10-03 的值（`backend/engine/hr_profile.py:14–16` 的註解） | 已驗證（程式註解） |
| 最大心率 185 | 同一個欄位在 2026-10-04 12:54 UTC 的值（本機 `athlete.coros_profile`） | 已驗證。一天內從 202 變 185，是誰改的沒查到 |
| 最大心率 189 | 本機 `plan.json` 的 `mhr` 列，2026-10-04，備註「設定頁手動輸入」 | 已驗證。單上說它來自「撐住 120 秒的實測」 |

注意：這些是**本機**資料庫（`~/.wko5coach/`）的值。如果正式環境在別台機器上跑，數字可能不同。

## 2. COROS 這一側

### 2.1 app 讀了什麼

- 登入回應和每次同步後的 `GET /account/query`（唯讀），解析 `zoneData` 的 `maxHr`、`rhr`、`lthr` 和三組區間表，存在設定 `athlete.coros_profile`（`backend/sync/coros_client.py:285–311`、`backend/engine/hr_profile.py:108`）。
- 只存最新一筆，**沒有歷史**。COROS 的值什麼時候變、變多少，事後查不到。
- 程式裡**沒有任何寫入帳號設定的端點**。寫入只有課表（`/training/schedule/update`、`/training/program/*`）。

### 2.2 推課表時心率怎麼編碼

`backend/sync/coros_workouts.py:517–525`：

- `intensityType 2`、`hrType 3`（LTHR 區間制）、`isIntensityPercent false`。
- `intensityValue / intensityValueExtend` = 絕對 bpm。
- 同時送 `intensityPercent = bpm ÷ app 的 LTHR × 100000`。

### 2.3 COROS 收到之後怎麼存（讀回資料）

本機有 SP-37 探測腳本存下的課表讀回（`~/.wko5coach/research/coros-tl/20261004-204104/planned.csv`，只含結構欄位）。把每一步的 bpm 除以百分比，可以反推 COROS 用的 LTHR：

| 日期 | 來源 | bpm | COROS 存的百分比 | `isIntensityPercent` | 反推的 LTHR |
|---|---|---|---|---|---|
| 9/30、10/1（已完成） | 行事曆 | 116–138 | 63–75 % | false | 184 |
| 10/4（已完成） | 行事曆 | 141–163 | 77–89 % | false | 183 |
| 10/4 三筆 | Training Hub 手動建的 | 138–144、145–155、162–185 | 91–95、96–102、107–121 % | **true** | 151–153 |
| 10/5 LSD | **app 推送**（`coros_plan_push` 有紀錄，10/4 14:36 UTC） | 131–151 | 86–99 % | false | 152.4 |
| 10/10 | 行事曆 | 131–151 | 86–99 % | false | 152.4 |

讀出來的事（已驗證，樣本很少）：

- app 推的課，bpm 原樣保留。如果 COROS 用的是 app 送的百分比，10/5 那筆應該是 82–94 %（131、151 ÷ 160）；實際存的是 86–99 %，等於 ÷ 152。所以 **COROS 丟掉 app 送的百分比，用自己的 LTHR 重算**。
- 百分比是整數百分點，所以反推的 LTHR 有 ±1 bpm 的誤差。
- 9/30 的課反推出 184，和 COROS 當時的 LTHR（182）對得上；10/5 的反推出 152。COROS 的 LTHR 在這幾天之間換過。
- Training Hub 裡用「% LTHR」建的課（`isIntensityPercent true`），bpm = 百分比 × 152。這就是「從課表心率目標反推出 152」的來源。**那是 COROS 自己建的課，不是 app 推的課**。

還沒確認的（待實測）：

- 手錶錶面上顯示的是 bpm 還是區間名稱。
- COROS 說「閾值更新後，行事曆上未來的課會自動調整」。這會不會動到 `isIntensityPercent false` 的課，沒有資料可以判斷。要測的方法：等 COROS 的 LTHR 下一次變動後，再讀回一次已推送的未來課表，看 bpm 有沒有變。

### 2.4 外部來源

| 來源 | 等級 | 說法 | 驗證 |
|---|---|---|---|
| COROS〈Heart Rate Zones: The Ultimate Guide〉 https://coros.com/stories/coros-metrics/c/coros-heart-rate-zones-the-ultimate-guide | 官方 | 三種區間模型（乳酸閾、儲備心率、最大心率），各 6 區；乳酸閾區間 <80／80–90／90–95／95–102／102–106／>106 % | 已驗證。和 app 讀到的帳號比例一致 |
| COROS〈How to Use the Running Fitness Test〉 https://coros.com/us/stories/coros-metrics/c/running-fitness-test | 官方 | 測試後得到新的閾值心率和最大心率，「EvoLab automatically applies these results」；「any upcoming workouts on your calendar will automatically adjust」；平常「EvoLab works in the background, updating your fitness metrics as you train」 | 已驗證 |
| COROS 支援〈April 2023 Update Common Questions〉、〈Setting Resting and Max Heart Rate Data〉 | 官方 | 最大心率和靜息心率可以手動設定（Profile → Settings → Heart Rate Zones）；「the lactate threshold value can only be automatically adjusted by the algorithm」；可以編輯預設區間 | **未驗證**（支援網站回 403，只看到搜尋摘要） |
| coroslab/COROS-MCP https://github.com/coroslab/COROS-MCP | 官方（repo 自稱是 COROS 官方 MCP） | 寫入工具只有課表和訓練計畫；有 `queryUserInfo`、`queryFitnessAssessmentOverview`，**沒有修改心率區間、最大心率、LTHR 的工具** | 已驗證（README）。「官方」是 repo 自己的說法，我沒有另外確認 |
| 同 repo issue #9 | 社群 | 建課表支援的強度類型是 % LTHR 和越野的 effort pace | 已驗證 |
| CuberL/coros-mcp https://github.com/CuberL/coros-mcp | 社群 | 列出的端點都是讀取；`/profile/private/query` 回個人資料和區間 | 已驗證（README） |
| cygnusb/coros-mcp https://github.com/cygnusb/coros-mcp | 社群 | 寫入只有課表範本和排程，沒有改個人資料或區間 | 已驗證（README） |
| Lu et al. 2025, *Front Physiol* 16:1621996, DOI 10.3389/fphys.2025.1621996 | 同儕審查 | 手錶估的 LTHR 對照遞增運動測試：COROS Pace 3（n = 17）平均偏差 +5.44 bpm、MAE 8.93 bpm、MAPE 5.95 %、**r = 0.13**；Garmin FR265（n = 23）MAE 11.44、r = 0.67；Huawei（n = 100）MAE 10.66、r = 0.36 | 已驗證（PMC 全文頁）。app 的 `threshold_confidence.py:18` 已經引用同一篇 |

來源之間沒有互相矛盾。要注意的是「沒有找到寫入端點」不等於「不存在」：COROS 手機 app 一定有改最大心率的請求，只是公開的專案都沒有記錄它。

## 3. 兩邊不一致時會影響什麼

| 項目 | 用誰的閾值 | 現在差多少 | 影響 |
|---|---|---|---|
| app 推送的課表心率目標 | app（絕對 bpm） | 0 | 沒有影響（§2.3） |
| 在 COROS 裡用 % LTHR 建的課 | COROS（152） | 比 app 低 5 %：95–100 % 是 144–152，app 的同一區是 152–160 | 只影響在 COROS 端自己建的課 |
| 手錶、COROS app 顯示的心率區間 | COROS | 乳酸閾模型：152 → 122／137／144／155／161；160 → 128／144／152／163／170，差 6–9 bpm | 同一次跑步，手錶和 app 顯示的區間時間不同 |
| app 的 hrTSS | app（160） | 用 152 算會多約 11 %（(160÷152)²） | app 沒有用 152，所以只是「如果統一成 COROS 的值」的後果 |
| COROS 的訓練負荷 TL | COROS | 算法沒有公開（未驗證）。如果 TL 用到它自己的區間，LTHR 從 182 改成 152 時 TL 的尺度會跟著變 | 見下 |
| TL ↔ TSS 換算（SP-37／38） | 兩邊都有 | `since_threshold_change` 只看 **app** 的閾值變動（`backend/engine/coros_tl.py:459`） | COROS 端的閾值變了，舊樣本不會被切掉（推估會讓擬合變差） |
| 課表心率區間選「儲備心率」時的 Z2 | 最大心率、靜息心率 | 靜息 53：最大心率 185 → 131–151；189 → 133–154；202 → 141–163 | 最大心率用哪一個，輕鬆跑上限差到 12 bpm |

補充：

- `coros_tl.py` 檔頭寫「TSS 的尺度跟著閾值動，手錶的 TL 不會」。這句話對 app 的閾值成立，對 COROS 自己的閾值是否成立沒有證據。COROS 的 LTHR 這半年從 182 變成 152，是一個實際發生過的大變動。
- 本機讀回的 10/5 LSD 目標是 131–151，剛好等於「儲備心率 Z2、最大心率 185、靜息 53」。但本機現在沒有 `plan.hr_zone_model` 這個設定（預設是乳酸閾模型，Z2 應該是 128–144）。推送當時用的是哪個模型，我沒有查到底。

## 4. 方案比較

| 方案 | 可行性 | 風險 | 已推送的課要不要重推 |
|---|---|---|---|
| (a) app 把閾值寫回 COROS | LTHR：大概不行（COROS 自己都不讓使用者手動改，未驗證）。最大心率、靜息心率：手機 app 可以改，所以端點存在，但沒有公開紀錄（待實測） | 高。要逆向手機 app 的請求；寫的是帳號個人資料，寫錯會影響手錶上所有指標；COROS 的演算法之後還是會自己改回去 | 不用（目標是絕對 bpm） |
| (b) app 改用 COROS 的值 | 技術上已經讀得到 | 高。COROS 的 LTHR 和實驗室值相關很低（Lu 2025），而且會自己大幅變動（182 → 152）；app 的 hrTSS、區間、5 區解鎖都會跟著跳 | 要（所有心率目標都會變） |
| (c) 推課表一律用絕對 bpm | **已經是現況**，讀回資料證實有效 | 低。唯一沒確認的是 COROS「自動調整未來課表」會不會動到它（待實測） | 不用 |
| (d) 提示使用者手動改 COROS | 最大心率、靜息心率可以；LTHR 不行，但可以改用自訂區間邊界（未驗證） | 低 | 不用 |

## 5. 建議

**app 當唯一基準，維持絕對 bpm，不寫回 COROS，用提示補上顯示的落差。** 也就是 (c) ＋ (d)。

具體要做的事，依優先順序：

1. **對照卡**（設定 → 心率）。並排顯示 app 和手錶的 LTHR、最大心率、靜息心率，差距超過門檻時標出來，並寫明「課表目標用的是 app 的值，手錶上的區間顯示用的是手錶的值」。門檻要你決定；我建議 LTHR 差 ≥ 5 bpm、最大心率差 ≥ 5 bpm（推估，Lu 2025 的 COROS 平均誤差約 9 bpm）。
2. **給使用者照著改的數字**。最大心率和靜息心率：直接顯示 app 的值，請使用者到 COROS app 改。LTHR 不能改：顯示 app 的乳酸閾區間五條邊界（128／144／152／163／170 這種），請使用者填進 COROS 的自訂區間。自訂區間能不能這樣用是未驗證，做之前要先在 COROS app 上確認。
3. **保存 COROS 閾值的歷史**。`athlete.coros_profile` 現在只留最新一筆。改成每次有變動就多記一列（日期、LTHR、最大心率、靜息心率），之後才查得到它什麼時候變。
4. **TL 換算在 COROS 的 LTHR 變動時也重新起算**。`since_threshold_change` 加上 COROS 端的閾值（需要第 3 項的歷史）。這條是推估，COROS 的 TL 算法沒有公開；做之前可以先用現有的 84 筆樣本檢查：把 6 月前後分開擬合，看係數有沒有明顯不同。
5. **不要再送 `intensityPercent`**，或在註解寫明它會被 COROS 覆蓋。現在送的值沒有作用，留著只會讓人誤以為 COROS 用 app 的 LTHR 算百分比。這是小事。
6. **最大心率的順位不用改**。手動設定優先於手錶，手錶優先於推估，和這次看到的情況（手錶一天內從 202 變 185）相容：使用者手動填了 189 之後，手錶的值就不再影響課表。

不建議做的事：

- 不要把 COROS 的 LTHR 拉進來當 app 的閾值（方案 b）。它可以繼續當「閾值可信度」的一個訊號（`threshold_confidence.py` 已經這樣用）。
- 不要去逆向寫入帳號設定的端點（方案 a）。就算最大心率寫得進去，LTHR 還是對不上，而且 COROS 會自己再改。

## 6. 要你決定的事

1. 同意「app 當基準、不寫回 COROS」嗎？
2. 對照卡的提醒門檻（我建議 LTHR 和最大心率各 5 bpm）。
3. 要不要做第 4 項（TL 換算跟著 COROS 閾值重新起算）。它需要先做第 3 項。
4. 要不要我實測兩件待實測的事：(i) COROS 的 LTHR 變動後，已推送的未來課表 bpm 有沒有被改；(ii) COROS app 的自訂區間能不能填任意邊界。兩件都只需要讀取和你在手機上看一眼，不需要寫入。

## 7. 限制

- 所有數字來自本機 `~/.wko5coach/`（資料庫唯讀、`plan.json`、SP-37 的探測輸出）。正式環境如果在別台機器，值可能不同。
- 這次沒有對 COROS 發任何請求。「能不能寫入區間設定」只能從公開專案和官方文件判斷，結論是「沒有找到」，不是「確定不能」。
- COROS 支援網站的兩篇文章回 403，「LTHR 只能自動調整」「可以編輯預設區間」這兩句只看到搜尋摘要，是未驗證。
- 讀回資料只有 15 列課表，其中確定是 app 推送的心率課只有 1 列（10/5 LSD）。「COROS 保留 bpm、重算百分比」這個結論靠的是這 1 列加上 4 列行事曆課的一致性。
- `hrZoneType = 2` 代表哪一種區間模型沒有查到。
- 最大心率在 10/3 到 10/4 之間從 202 變成 185 的原因沒有查到（可能是使用者改的，也可能是 COROS）。
- app 的 LTHR 160 沒有重算，只確認了它的來源路徑。
- COROS 的 TL 算法沒有公開文件，§3 關於 TL 的部分是推估。

## 參考

- Lu C, Cui W, Zhu Z, et al. Validity of smartwatch-derived estimates of lactate threshold heart rate and pace compared to graded exercise testing. *Front Physiol* 2025;16:1621996. DOI 10.3389/fphys.2025.1621996. https://pmc.ncbi.nlm.nih.gov/articles/PMC12309276/ （2026-10-05 讀取）
- COROS. COROS Heart Rate Zones: The Ultimate Guide. https://coros.com/stories/coros-metrics/c/coros-heart-rate-zones-the-ultimate-guide （2026-10-05 讀取）
- COROS. How to Use the COROS Running Fitness Test. https://coros.com/us/stories/coros-metrics/c/running-fitness-test （2026-10-05 讀取）
- COROS 支援：April 2023 Update Common Questions https://support.coros.com/hc/en-us/articles/15705566771604 ；Setting Resting and Max Heart Rate Data https://support.coros.com/hc/en-us/articles/360040258391 （未驗證，403）
- coroslab/COROS-MCP https://github.com/coroslab/COROS-MCP ；issue #9 https://github.com/coroslab/COROS-MCP/issues/9
- CuberL/coros-mcp https://github.com/CuberL/coros-mcp ；cygnusb/coros-mcp https://github.com/cygnusb/coros-mcp
- 既有文件：`docs/research/zones-and-thresholds.md` §1（COROS 帳號那列被忽略的原因）、`docs/spec/wko5-coros-sync.spec.md`（COROS 心率設定、TL 換算）、`docs/spec/overview.spec.md`（課表心率區間）。
