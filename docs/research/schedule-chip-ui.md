# 課表小卡 UI 重設計：盤點與方向（SP-375 第一輪）

> 日期：2026-10-12。只有研究與設計，沒有改程式。盤點靠讀程式碼（分支 `claude/exciting-archimedes-ubl664` `257eef9`），沒開瀏覽器。
> 使用者定的方向：**完成度用顏色、課的類型用 icon、未來（還沒做）的課只畫外框**。本文在這個方向內出三個變體，mockup 在 `schedule-chip-ui-mockup.html`。
> 第二輪（挑定變體後）才寫：圖例定稿、每個元素的去留與搬家清單、遷移計畫。

## 1. 現況盤點（摘要）

完整表（每個元素的編碼、資料欄位、行號）見 Obsidian task SP-375「現況」；這裡只列會影響設計的事。

| 問題 | 證據 |
|---|---|
| 一張卡最多 8 個獨立標記 | 未來課：kind 顏色、暫定虛線、「熱」、↻／✎、手錶 icon、中繼列、target、detail。完成課：kind 左邊、完成度底色＋內框、✓、≠／◐、實際行、⚠ note（`schedule.html:1073-1102`） |
| 完成度底色蓋掉類型底色 | `.chip.st-done[class*="cp-"]` 把底色換成 `--cpc`，類型只剩左邊 3px（`:257`） |
| 同一色相代表 4～6 件事 | 橘＝強度課、專項期、越野活動、≠／◐、完成度黃、手錶需更新；藍 `--accent` 與 `--s1` 數值相同＝LSD、基礎期、今天、選取、↻ |
| 完成度只靠顏色 | ✓ 換色表示綠黃紅，無形狀差；≠ 與 ◐ 同色只差字形。不符 WCAG 2.2 SC 1.4.1 |
| 右上角三組符號擠同一格 | `.c-r` 放 ≠／◐、↻／✎、手錶 icon，字級相同 |
| 同一資訊重複 4～6 處 | 完成度字串 4 處；「暫定」6 處；「未完成」4 處；門檻數字硬寫 3 處 |
| 兩頁配色對不上 | 總覽 `.ty-*` 與課表 `.k-*` 用同一組 `--s*`，對應不同課 |
| 總覽已有類型 icon，課表沒用 | `dashicons.js`：easy 心、z3 折線、z5 閃電、long 山、hike 山＋勾、test 燒杯、strength 槓鈴、race 旗、heat 太陽、rest 月亮 |

順帶的 bug（可在實作單一起修）：`.k-notice` 沒定義 `--k`（`:427`）；總覽 tooltip 遇 `not_pushed` 露出原始 key（`overview.html:659`）；`.adapt-note` 色硬編碼 `#b36b00`（`:426`）；`.mini.*` CSS 無人用（`overview.html:132-133`）。

## 2. 使用者看課表時在問什麼（依頻率）

| # | 問題 | 畫面 | 層級 |
|---|---|---|---|
| 1 | 今天／這幾天要練什麼、多久 | 課表、總覽本週 | 卡上：類型 icon＋標題＋分鐘 |
| 2 | 這週練得怎樣 | 課表、總覽 | 卡上：完成度顏色＋形狀；週統計一個百分比 |
| 3 | 哪堂沒照做、為什麼 | 課表 | 卡上只給形狀（◐／≠），原因 hover／點開 |
| 4 | 手錶上的是不是最新的 | 課表（未來課） | 右上角手錶 icon；dialog 寫狀態文字 |
| 5 | 哪些課被系統動過 | 課表、總覽 | icon 角落小徽章 ↻／✎，細節點開 |
| 6 | 這週是哪個期、暫定嗎 | 課表 | 週層級（左條、週統計），不放卡上 |

原則：每個維度一種編碼。類型→icon；完成度→顏色＋形狀；做了沒→外框 vs 填色；誰改的→角落徽章；手錶→右上角 icon。類型**不再用顏色**。

## 3. 完成度：Linear 風格的圓圈（使用者 2026-10-12 定）

不用 ✓／◐／≠ 字形，改用一組圓圈 icon（Linear 的 issue 狀態圖示）：**check 圈**＝完成（±20%）；**pie 圈**＝部分，填滿比例＝實際 ÷ 計畫的百分比（>100% 封頂）；**slash 圈**＝沒照課表（換運動、跑反強度）；**x 圈**＝未完成；**空圈**＝還沒做；**點線圈**＝暫定。顏色仍照完成度（綠／黃／紅／灰）。放在卡片**右欄**（和未來課的手錶 icon 同一欄，圓圈在前），不疊在類型 icon 方塊上——方塊只放類型 icon＋完成度著色，角落只剩 ↻／✎ 小徽章（使用者 2026-10-12：角標會擋到類型 icon）。

### 3.1 分級對照（提議，動到規則）

| 現在 | 提議 | 形狀 | 顏色 |
|---|---|---|---|
| green（±20%） | 完成 | ✓ | `--good` |
| yellow（20–50%）＋ plan_match `short`（強度不足／偏強／項目可替代） | 部分 | ◐ | `--watch` |
| red（>50%、類型不符）＋ `off_plan`（跑反強度、換運動） | 沒照課表 | ≠ | `--bad` |
| missed | 未完成 | 劃線、灰外框 | `--faint` |

`compliance.with_plan_check` 已把 short→至少 yellow、off_plan→至少 yellow；改成 off_plan→red 是一行門檻，統計頁 `compliance.html` 的分類文字要跟著改。門檻數字只留在一顆 `?` 的圖例裡。

## 4. 三個變體（mockup 可切換）

共通：卡＝類型 icon ＋ 標題 ＋ 中繼列。未來的課只有外框（暫定＝虛線）、灰字，右上角放手錶 icon，icon 角落放 ↻／✎。完成的課右上角清空。課表外活動＝點線外框。

| | V1 左側色條 | V2 整張填色 | V3 icon 方塊上色 |
|---|---|---|---|
| 完成度塗在 | 左邊 4px 色條＋形狀在右 | 整張淡底＋邊框＋形狀在右 | 22px icon 方塊實色、白 icon，形狀是方塊角標 |
| 一眼掃這週 | 中 | **強** | 中強 |
| 安靜度 | **最安靜**（TP 手機版） | 最吵 | 安靜 |
| 未來 vs 完成的對比 | 弱 | **最清楚** | 清楚（空框 icon vs 實色 icon，= COROS） |
| 手機 7 欄 | OK | OK | OK，icon 可單獨成卡 |
| 深色 | OK | 填底比例要重調 | OK |
| 改動量 | 小 | 小 | 中；元件可與總覽 `.sx .ic` 共用 |

**定案 V3**（使用者 2026-10-12）。方塊顏色用比狀態色更深的一組 `--tile-ok #166534`／`--tile-part #92400e`／`--tile-off #991b1b`（深色主題 `#15803d`／`#b45309`／`#b91c1c`），白 icon 對比淺色 ≥ 6.9:1、深色 ≥ 4.5:1；形狀與文字仍用 `--good/--watch/--bad`。原建議：完成度與類型住同一個方塊，卡片本體乾淨，和總覽的 icon 方塊是同一個元件，兩頁一致。要「一眼看完成度」優先就選 V2（最像 TrainingPeaks）。

## 5. 參考

- TrainingPeaks Workout Card：綠 ±20%、黃 50–79／121–150%、橘 >50%、紅沒做、灰計畫外；手機版為左側色條；可關閉 — https://help.trainingpeaks.com/hc/en-us/articles/204861204
- COROS Updated Training Calendar View：未來課只畫類型色外框，完成填實色，右上角準確度分數 — https://support.coros.com/hc/en-us/articles/6174277655316
- intervals.icu compliance：另一條色帶，80–120 綠、50–150 橘 — https://forum.intervals.icu/t/compliance-activity-field/47805
- WCAG 2.2 SC 1.4.1 Use of Color — https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html
- NN/g Icon Usability：icon 旁要有文字或 tooltip — https://www.nngroup.com/articles/icon-usability/
- repo：`docs/research/ui-hint-audit.md`（SP-305）§1.3 課表列、`docs/research/competitor-charts.md` compliance 列
