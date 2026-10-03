# 跑步 FTP 自動帶入 TSS（COROS／TP 來源）

Status: todo（還沒開工）
Type: bugfix
建立：2026-10-03

## 問題

COROS 來源的 9/17 跑步（NP 156 W、45 分）TSS = 51，WKO5 = 29。

- 原始資料一致：NP 156.2 vs 155.9、時間、距離都相同。
- COROS 來源**沒有跑步 FTP**，所以算不了功率 TSS，退回 hrTSS（自動估的 LTHR 156）。
- 算 TSS 用的 FTP 只看 `w.entry.ftp` 或 `sport_setting("ftp")`，也就是 WKO5 設定或 DB 的 `run_ftp_w`
  （`backend/engine/wko5expr/dataset.py:628`）。計畫裡的 CP 測試和 Stryd PD 推估的 CP **都沒接到 TSS**：
  - 計畫的 CP 測試：`Dataset.setting()` 對計畫只查 `thr`／`mhr`（`dataset.py:584-588`）。
  - Stryd PD 推估（`FitFolderDataset._estimate_cp`、`cp()`）：註解寫明「does not change any TSS」，
    `_estimate_settings` 也刻意不填 runftp（當年 PD 擬合混到手錶功率，估成 1.8 倍）。
- 附帶 bug：`_is_hr_sourced`（`dataset.py:537`）只看有沒有 NP，沒看功率 TSS 是否真的算出來。
  退回 hrTSS 的跑步因此沒套到「只算移動時間 hrTSS」和爬升加成。

## WKO5 是怎麼決定 FTP 的（已查過使用者的 WKO5 資料）

- WKO5 算 TSS 用的是**運動員設定裡、依日期生效的 FTP**（每筆 workout 存的 3010 欄位 = 當時的 FTP），
  **不是** PD 模型的 mFTP。
- 使用者的 WKO5：runftp 只有一筆 `(1980-01-01, 250)`，440 筆跑步（2023-02 → 2026-09-30）全部都用 250。
  也就是說，是手動設定、從沒更新過。
- WKO5 的 PD 模型另外算 mFTP（目前快照：**mFTP 195.4 W**、FRC 4.9 kJ、TTE 31 分），不會拿來算 TSS。
- 本 repo 已經有 WKO5 mFTP 的移植：`racepower/athlete.py:pd_model`，驗證過 175.7 vs 快照 175.6
  （`docs/research/cp-test-protocols.md §1B.2`）。`cp_as_of`、`_pd_mftp` 是依日期回推的版本。

⇒ 「跟 WKO5 一致」有兩層意思，要分開做：
1. **比對模式**：用跟 WKO5 一樣的 FTP（WKO5 設定的 250），TSS 就會一樣（9/17 用 250 算 ≈ 29.3）。這是公式對齊的驗證。
2. **自動模式**：FTP 用 WKO5 的方法自動算，也就是 **WKO5 PD 模型 mFTP**（只用 Stryd 功率）。
   WKO5 本身沒有自動把 mFTP 寫回 FTP，這部分是我們補上的，要標「推估」。
   9/17 的推估值約 175 W，算出來 TSS ≈ 60，**不會等於 WKO5 的 29**，因為 WKO5 的 250 本身是舊的手動值。
   UI 要把這個差異講清楚。

## 修正內容

### 1. 跑步 FTP 的來源順序（`FitFolderDataset`，只影響 COROS／TP 來源）

依日期取第一個有值的：
1. 計畫的 CP 測試（`plan.threshold_on("cp", day)`），標「你的測試 YYYY-MM-DD」
2. DB 的 `athlete_settings.run_ftp_w`
3. **Stryd-only PD 模型 mFTP**（`_cp_fit_on(day)`，已經存在），標「推估：Stryd PD 模型 mFTP」
4. 都沒有 → 不算功率 TSS（照現在的流程退回 rTSS／hrTSS）

做法：在 `FitFolderDataset` override `sport_setting("ftp", w)`，跑步就走上面的順序；或在 `_metrics` 改成用 `self.cp(w)`。
選一個就好，**算 TSS 的 FTP 和圖表用的 CP 要是同一個值**。

### 2. 手錶功率一律不用

- 算 FTP（PD 擬合）只用 `power_source == STRYD` 而且 `power_ok` 的跑步。`_estimate_cp` 已經這樣做，要加測試鎖住。
- 手錶功率的跑步沒有功率 TSS：沿用 `accept_watch_power=False` → `_power_blocked`，退回 rTSS／hrTSS。
- 拿掉 `_estimate_settings` 裡「不填 runftp」的理由（那時 `cp_as_of` 會混手錶功率），改用 Stryd-only 的 `_cp_fit_on`。
  `cp_as_of` 不要拿來算 TSS。

### 3. 修 `_is_hr_sourced`

改成判斷 TSS 實際是怎麼算出來的：在 `_metrics` 回傳 `tss_source`（power／rtss／hrtss／tp），
`_is_hr_sourced` 和 `api/wko5views.py:588` 都改讀這個欄位。

### 4. WKO5 比對（parity）

- `config.parity=True` 或 `charts.fit_settings_from_wko5=true` 時：FTP 用 WKO5 設定（現有行為），不用推估值。
- 對照表（sourcecompare）每筆活動要顯示：FTP 用了哪個值、來源是什麼、TSS 算法。

### 5. 快取失效

FTP 改變會讓所有 TSS 跟著變 → `_dataset_cfg` 的 key 和 PMC 序列快取要包含 FTP 來源的簽章。
計畫改 CP 時，`plan_changed(thresholds=True)` 已經會清快取，要確認 cp 也有觸發。

## 測試（不能依賴 WKO5 資料夾，用合成 FIT／fixture）

- 有 Stryd 功率、沒有 CP 測試 → TSS 用 PD mFTP 算，來源標「推估」。
- 有計畫 CP 測試 → 測試日以後用測試值，以前用推估值。
- 手錶功率的跑步 → 沒有功率 TSS，也不會被拿去擬合 FTP。
- 退回 hrTSS 的跑步 → 有套到移動時間 hrTSS 和爬升加成（`_is_hr_sourced` 修正）。
- parity 模式 → FTP 用 WKO5 設定的值，TSS 等於 WKO5 的公式（NP² × dur ÷ (FTP² × 36)）。
- 只跑相關的測試檔，全套留到最後。

## 手動驗收

- 9/17：COROS 來源顯示 FTP ≈ 175（推估）、TSS ≈ 60；WKO5 比對模式顯示 FTP 250、TSS ≈ 29。
- 9/30 之後的跑步：FTP = 204（你的測試）。
- PMC 看 2026-09 前後的 CTL 變化，跟改之前比，記下差多少。

## 第二部分：最大心率、靜息心率、心率區間模型（使用者 2026-10-03 加入）

### 現況

- 最大心率：沒有自動算，只有計畫的 `mhr` 欄位（手動）。COROS 帳號的值不採用。
- 靜息心率：沒有任何來源，所以 RQ 儲備心率區間一直顯示「算不出來」。
- 區間：預設 Friel % LTHR；單次活動圖可以換 Friel／Classic／Seiler 3／RQ HRR；區間時數圖只有 Friel／Classic／Seiler 3。
  2026-10-01 曾決定「不做 %HRmax 區間」（Iannetta 2020）→ **使用者 2026-10-03 改要可選**，預設維持 Friel，%HRmax 的 ? 說明要寫限制。
- FIT 檔裡沒有 zone／user_profile 訊息（查過 COROS FIT），所以區間設定只能從 COROS 帳號 API 或使用者設定拿。

### 使用者的 COROS 設定（驗證用）

最大心率 202、儲備心率區間 Z2 = 141–163。用 RQ 表（E = 59–74% HRR）反推，靜息心率 ≈ 53，
上下緣都對得上（53 + 0.59×149 = 141，53 + 0.74×149 = 163）。⇒ **COROS 的 HRR 區間就是 `RQ_HRR_ZONES`**。

COROS 三種模型，各 6 區（Recovery／Aerobic Endurance／Aerobic Power／Threshold／Anaerobic Endurance／Anaerobic Power）：
- 乳酸閾值心率：<80／80–90／90–95／95–102／102–106／>106 % LTHR（coros.com「COROS Heart Rate Zones: The Ultimate Guide」）
- 儲備心率：59／74／84／88／95 % HRR（從使用者的數字驗證過）
- 最大心率：**百分比還沒查到官方數字**。先從 COROS 帳號 API 的 `zoneData` 讀（登入回應裡有，現在只讀 lthr／ftp）；
  讀不到的話，就請使用者從 COROS app 抄 6 個區間的 bpm。不能用猜的。
  → 已讀到：`maxHrZone` 50／60／70／80／90／100%（見下方「第二部分進度」）。

### 改法

1. **最大心率自動估**（`thresholds.py` 新增 `estimate_mhr`）：近 365 天跑步，每次取「持續 ≥ 5 秒」的最高心率，
   排除光學心率的尖刺（例如一秒跳 > 15 bpm、超過 220）。取最高值，標「推估」。
   使用者資料裡有 223、220 這種尖刺，要被濾掉；可信的大約 202–209。
2. **設定頁**（照「設定以自動為主」）：最大心率、靜息心率顯示「自動值 + 來源」，可以手動覆寫（有日期）。
   - 靜息心率的來源：手動 > COROS 帳號 `zoneData`（標「來自手錶」）> 未設定。
   - 最大心率的來源：手動 > 自動估 > COROS 帳號。
3. **心率區間模型設定**：設定頁可以選全站預設的心率區間：
   COROS 乳酸閾（6 區）、COROS 儲備心率（6 區）、COROS 最大心率（6 區）、Friel 7 區、Classic 5 區、Seiler 3 區。
   - 預設 Friel。各圖表原本的「記住上次選的」仍然保留，沒選過就用全站預設。
4. **相關圖表全部加上新模型**：單次活動的心率區間、區間時數（`period_zones.HR_IDS`）、WKO5 區間表（`zones.SYSTEMS`）。
   HRR／HRmax 沒有資料時，顯示原因（「沒有靜息心率，到設定填」）。
5. **AeT 自動估失敗時**：顯示「飄移 < 5% 的最高心率」當參考（使用者資料是 162），
   讓人知道輕鬆跑上限可能比 0.89 × LTHR 高。**課表的輕鬆跑上限仍然用 AeT**，不跟著選的區間走（待使用者決定）。

### 測試

合成心率資料：尖刺要被濾掉、最大心率的估算、HRR 的區間邊界（202／53 → Z2 141–163）、
COROS LTHR 表、設定覆寫的優先順序、沒有靜息心率時顯示原因。

### 第二部分進度（2026-10-03，branch feat/hr-zones-settings）

使用者同日改範圍：**課表心率區間（設定）和圖表的心率區間是兩件事**。

- [x] COROS 帳號 `zoneData`（GET /account/query 唯讀查一次）有 `maxHr` 202、`rhr` 53、`lthr` 182，
  以及三種模型各 6 個上緣：`lthrZone` 80／90／95／102／106、`rhrZone` 59／74／84／88／95（＝RQ）、
  `maxHrZone` **50／60／70／80／90**（官方沒公開，這是帳號裡的值；照另外兩組的讀法，Z1 < 50%，推估）。
  登入回應與每次同步後（`coros_client.store_hr_profile` / `refresh_hr_profile`）存到
  user_settings `athlete.coros_profile`，標「來自手錶」；COROS 的 ratio 優先於預設表，bpm 四捨五入，
  同樣的最大／靜息心率時跟手錶一致。
- [x] `thresholds.estimate_mhr`：近 365 天每次跑步「持續 ≥ 5 秒」的最高心率；丟掉 < 30、> 220、
  一秒跳 > 15 bpm 的尖刺（直到回到跳之前 +15 以內）；最高那次比第二高多 > 5 bpm 就當誤差。
  ≥ 3 次跑步才有值，標推估。使用者資料：**191**（182 次；3/25 的 218 是鎖到步頻的假值，被丟掉；
  201–208 的峰值都在 1–2.6 年前）。
- [x] 來源順序（`engine/hr_profile.py`，所有圖表和課表共用）：
  - 最大心率：你的設定（計畫門檻 `mhr`，有日期）> **推估和手錶取高的**（推估是下限，訓練很少跑到最大心率；
    所以使用者目前是手錶的 202，來源文字註明近 365 天最高 191）。
    ⚠ 跟原本「設定 > 推估 > 手錶」不同：照原順序會用 191，HRR 區間比手錶低一截。
  - 靜息心率：你的設定（計畫門檻新欄位 `rhr`）> 手錶 > 沒有（「沒有靜息心率，到設定填」）。
- [x] 設定頁「心率」：最大／靜息心率顯示目前值和來源；進階設定可手動覆寫（存成今天的計畫門檻列，
  改回自動 = 清掉 mhr／rhr）。API：`GET/PUT /api/v1/plan/hr-profile`。
- [x] A. **課表心率區間**（`plan.hr_zone_model`：lthr 預設／hrr／hrmax）只管課表的心率目標：
  `zones.training_targets`、總覽的輕鬆跑上限、`coros_workouts` / `workout_steps` 的 easy_hr 和間歇心率。
  對應：恢復 Z1、輕鬆／長跑／越野 Z2、Z3sub→Z3、Z3near（閾值）→Z4、Z4（supra）與 Z5（VO2）→Z5。
  量到的 AeT（測試）一定當輕鬆跑上限；推估的 AeT、0.89 × LTHR 不再壓過模型。缺資料時改用乳酸閾值區間並顯示原因。
  推到 COROS 仍是 hrType 3 + 絕對 bpm（其他 hrType 代碼沒驗證，不猜）。編輯器裡使用者自己選的 Friel 區間不動。
  預設（乳酸閾）下使用者的目標變化（LTHR 155）：恢復 ≤132 → ≤124、輕鬆／長跑 ≤138 → 124–140（下緣 0.75 → 0.80 LTHR）、
  爬坡 140–155 → 140–158、爬坡重複 147–160 → 147–158、閾值 147–155 → 147–158、supra 155–160 → 158–164、VO2 160–164 → 158–164。
  非乳酸閾模型時總覽加一則說明「課表文字裡的『AeT』指這個上限」（76 處文字沒有改名）。
- [x] B. **圖表**：預設仍是 Friel（跟 WKO5 一致），各自記住；新增 COROS 乳酸閾／儲備心率（id 沿用 `rqhrr`）／最大心率
  到單次活動、區間時數、WKO5 區間表（`zones.SYSTEMS` 加 coroslthr／coroshrr／coroshrmax，心率區間表卡片可以切換）。
  %HRmax 的限制（Iannetta 2020）寫在模型的來源文字裡。
- [x] AeT 自動估失敗時，「0.89 × LTHR」的來源文字附「參考：飄移 < 5% 的跑步最高心率 162 bpm」
  （`racepower/athlete.thresholds_as_of`、`zones.training_targets`）；輕鬆跑上限不跟著改。
- [x] 測試：`backend/tests/test_hr_profile.py`（合成資料）。

待使用者決定：
- 最大心率要不要看更長的時間（例如 3 年）？近 365 天沒有最大努力，推估只有 191。
- `maxHrZone` 的 Z1 是 < 50% 還是 50–60%（要在 COROS app 對一下）。
- 課表文字的「AeT」在儲備心率／最大心率模型下要不要改名（例如「輕鬆跑上限」）。

## WKO5 的缺失（已確認，使用者 2026-10-03 定案）

- WKO5 算 TSS 用的是手動輸入的 FTP，不會自動用 PD 模型的 mFTP 更新。
  使用者的 runftp 從 2023 年起就一直是 250，但 mFTP 已經是 195、9/30 的 CP 測試是 204。
  結果 WKO5 的功率 TSS 整體低估：9/17 那次 29，用 mFTP 算約 44–60。
- 本 app 的自動模式**刻意跟 WKO5 不同**：TSS 用依日期的 mFTP（Stryd only）或使用者的 CP 測試。
  WKO5 只當「公式對齊」的比對基準（parity 模式用 WKO5 的 FTP），不當 FTP 數值的標準。
- UI：比對頁或活動頁的 FTP 來源旁，用 ? 提示說明「WKO5 用手動 FTP（250，未更新），本 app 用 mFTP／測試值，所以 TSS 不同」。
- 不需要使用者去改 WKO5 的設定。
