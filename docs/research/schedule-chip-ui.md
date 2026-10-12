# 課表小卡 UI 重設計（SP-375）

> 日期：2026-10-12。只有研究與設計，沒有改程式。盤點靠讀程式碼（main `e2d1519`，行號以它為準），沒開瀏覽器。
> 使用者定的方向：**完成度用顏色、課的類型用 icon、未來（還沒做）的課只畫外框**；變體選 **V1 左側色條**；完成度圖示用 Linear 風格圓圈；分級併成四種。
> §1–5 是第一輪（盤點、方向、三個變體）；**§6 起是第二輪的完整設計**：圖例定稿、去留與搬家清單、四個畫面、分級規則、遷移計畫。mockup 在 `schedule-chip-ui-mockup.html`（預設 V1，第二輪加了圖例、日格、agenda、dialog、總覽區塊）。
> **2026-10-12 與 SP-393（正向呈現）合流後的定案**：完成度**不用紅色**（只有綠／黃／灰，紅只留給手錶推送失敗）；「未完成」改叫**跳過**、畫**空圈＋灰字、不劃線**；「沒照課表」改叫**換了課**、黃 slash；自動調整規則 E **拿掉「連續 2 堂紅」觸發**（只看 TSB 與 CTL 爬升）。§6–11 已照這個改。

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

**定案改為 V1**（使用者 2026-10-12 稍晚改判）：完成度顏色塗在左邊 4px 色條，類型 icon 方塊不上色，完成度圓圈在右欄。V3 的方塊色（`--tile-*`）保留在 mockup 供對照。原 V3 說明：方塊顏色用比狀態色更深的一組 `--tile-ok #166534`／`--tile-part #92400e`／`--tile-off #991b1b`（深色主題 `#15803d`／`#b45309`／`#b91c1c`），白 icon 對比淺色 ≥ 6.9:1、深色 ≥ 4.5:1；形狀與文字仍用 `--good/--watch/--bad`。原建議：完成度與類型住同一個方塊，卡片本體乾淨，和總覽的 icon 方塊是同一個元件，兩頁一致。要「一眼看完成度」優先就選 V2（最像 TrainingPeaks）。

## 5. 參考

- TrainingPeaks Workout Card：綠 ±20%、黃 50–79／121–150%、橘 >50%、紅沒做、灰計畫外；手機版為左側色條；可關閉 — https://help.trainingpeaks.com/hc/en-us/articles/204861204
- COROS Updated Training Calendar View：未來課只畫類型色外框，完成填實色，右上角準確度分數 — https://support.coros.com/hc/en-us/articles/6174277655316
- intervals.icu compliance：另一條色帶，80–120 綠、50–150 橘 — https://forum.intervals.icu/t/compliance-activity-field/47805
- WCAG 2.2 SC 1.4.1 Use of Color — https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html
- NN/g Icon Usability：icon 旁要有文字或 tooltip — https://www.nngroup.com/articles/icon-usability/
- repo：`docs/research/ui-hint-audit.md`（SP-305）§1.3 課表列、`docs/research/competitor-charts.md` compliance 列

---

# 第二輪：完整設計

## 6. 設計原則（一句話版）

**顏色說「偏離多少」，形狀說「發生了什麼」，icon 說「是什麼課」，外框／填色說「做了沒」。** 每個維度只有一個通道，每個通道只回答一個問題：

| 問題 | 通道 | 值 |
|---|---|---|
| 是什麼課 | 類型 icon（22px 方塊，`dashicons.js`，不上色） | easy 心、z3 折線、z5 閃電、long 山、hike 山＋勾、test 燒杯、strength 槓鈴、race 旗、heat 太陽、rest 月亮、notice ⓘ、bike（新增）、other（新增：圓點） |
| 做了沒 | 卡片外框 vs 左側色條 | 未來／今天還沒做＝1px 外框、無色條；做完＝左側 4px 色條；未完成＝灰外框＋標題劃線 |
| 偏離多少 | 色條與圓圈的**顏色** | 綠 `--good`（完成）、黃 `--watch`（部分、換了課）、灰 `--faint`（跳過、還沒做）。**沒有紅**：紅只留給手錶推送失敗（SP-393） |
| 發生了什麼 | 右欄圓圈的**形狀** | check 完成、pie 部分（填到實際 ÷ 計畫，差多少看填滿比例）、slash 換了課、空圈 跳過／還沒做（差別只在卡片在過去）、點線圈 暫定 |
| 誰改過 | icon 方塊角落小徽章 | ↻ 系統（`--accent`）、✎ 自己（`--muted`） |
| 手錶上是不是最新的 | 右欄手錶 icon（只在未來課、**只在需處理時**） | 需更新 `--watch`、失敗 `--bad`；已推送／未推送**不畫** |
| 暫定 | 卡片外框虛線＋圓圈點線 | 只在卡上這一處；週的斜紋、週統計 tag、agenda、dialog 副標都拿掉（見 §8） |

不再存在的通道：**類型顏色**（`.k-*`、`--k`、`.ty-*`、`--s*` 在卡上全部退場）、**完成度底色**（`.chip.st-done[class*="cp-"]`）、**✓／◐／≠ 字形**（併進圓圈）。

## 7. 圖例定稿

目標：課表頁的圖例列收成一顆 `?`（`.legend` 只剩「`?` 圖例」一個按鈕，點開是下表；手機也不佔一列）。總覽本週清單共用同一張表（同一個 i18n key，`common.legend.*`）。

| 區 | 符號 | 意思 | 顏色 | 畫在哪 |
|---|---|---|---|---|
| 類型 | 心 | 輕鬆跑 | 無（`--type`） | icon 方塊 |
| | 折線 | 有氧間歇 / 閾值（z3） | | |
| | 閃電 | VO2max / 速度（z5） | | |
| | 山 | LSD / 長跑 | | |
| | 山＋勾 | 越野跑 / 健行 | | |
| | 燒杯 | 測試 | | |
| | 槓鈴 | 肌力 | | |
| | 旗 | 比賽 | | |
| | 太陽 | 熱適應（取代「熱」標籤） | | |
| | 月亮 | 休息 | | |
| | 單車（新） | 單車（課表外活動） | | |
| | ⓘ | 課表待確認 | | |
| 完成度 | ● check 圈 | 完成：時間和 TSS 都在計畫 ±20%，強度做到 80% 以上 | 綠 | 右欄 ＋ 左側色條 |
| | ◔ pie 圈（填到 %） | 部分：時間或 TSS 偏 20% 以上、強度只做 50–80%、偏強、跑反強度或換走跑項目（時間 TSS 都對）；差多少看填滿比例 | 黃 | |
| | ⊘ slash 圈 | 換了課：換走跑以外的運動、跑反強度且時間 TSS 也偏（hover 說換成什麼） | 黃 | |
| | ○ 空圈（過去） | 跳過：這天沒有對應的活動；卡片灰字、灰外框、**不劃線**、沒有色條 | 灰 | 右欄 |
| | ○ 空圈（未來） | 還沒做（今天以後） | 灰 | 右欄；卡片只有外框 |
| | ◌ 點線圈 ＋ 虛線外框 | 暫定（重排時會依進度調整） | 灰 | |
| 改過 | ↻ | 自動調整改過（原因與前後值在 hover／dialog） | `--accent` | icon 方塊右上角 |
| | ✎ | 你改過／自訂／復原 | `--muted` | |
| 手錶 | ⌚ 實心＋驚嘆 | 需更新：課表改過，再推送一次 | 黃 | 右欄（圓圈之後） |
| | ⌚ 紅 | 推送失敗（**畫面上唯一的紅**） | 紅 | |
| | （不畫） | 已推送／未推送 | — | dialog 文字 |
| 日格 | 點線外框卡 | 沒排課的活動（點一下配對） | 無 | 日格內 |
| | 45° 斜紋 | 不排課日期 | | 日格底 |
| | 淡綠底 | 休息日 | | |
| | 左邊 4px 色條 | 週期（基礎／專項／減量…）—— **週**層級，顏色沿用 `--ph` | 週期色 | 週列最左 |

門檻數字（±20%、50%、80%）**只寫在這張表**；chip tooltip、週統計 tooltip、總覽 tooltip 一律寫「完成度 92%（完成）」這種結果，不再附門檻（§9.4）。

### 7.1 無障礙

- 顏色永遠配形狀（WCAG 2.2 SC 1.4.1）：綠 check、黃 pie／slash、灰空圈；色條本身不獨立表意。黃色同時給「部分」與「換了課」，靠形狀分。
- 對比：`--good/--watch/--bad` 在淺色 `#1a7f37/#b45309/#c62828` on `#fff` ≥ 4.5:1，深色 `#4ade80/#fbbf24/#f87171` on `#1a1a19` ≥ 7:1；圓圈線寬 1.5px、尺寸 16px（AA 非文字對比 3:1 無虞）。
- `aria-label`（chip 已有 `lbl`）順序改成：**類型：標題，時間，TSS，完成度一句話，暫定，改過，手錶**——跟畫面的閱讀順序一致，圓圈與 icon 都 `aria-hidden`。
- 中英文：圖例與完成度文字全部走 i18n（`schedule.*`／`compliance.status.*`／新 `common.legend.*`），icon 不帶文字所以不受語言影響。
- 手機（7 欄月檢視，每欄約 50px）：方塊 22px＋圓圈 16px＋色條 4px 之後標題剩約 0 px → **月檢視手機版只畫方塊＋色條＋圓圈，標題收進 hover／點開**（現在也是標題被截到 1–2 字，等於沒有）。週檢視與 agenda 照桌機畫。

## 8. 去留與搬家清單

欄位：**留**＝留在卡上／**hover**＝只在 title tooltip／**dialog**＝只在課表對話框／**拿掉**。行號以 main `e2d1519` 為準。

### 8.1 課表 chip（`schedule.html` `chipHtml` :1073-1102、CSS :225-272）

| # | 現在的元素 | 回答什麼 | 處置 | 理由 |
|---|---|---|---|---|
| 1 | 類型顏色：左邊 3px `--k` ＋ 13% 底色（:226-228）、`.fam-*` 三個強度課家族色 | 是什麼課 | **拿掉 → 類型 icon** | 顏色通道讓給完成度；強度課家族用 z3／z5 兩個 icon 區分（aerobic→z3、vo2max／speed→z5），家族全名留 hover |
| 2 | 完成度底色＋內框（:257）、✓ 前綴（:254） | 偏離多少、做了沒 | **拿掉 → 左側 4px 色條（綠／黃）＋圓圈** | V1 定案；✓ 字形併進 check 圈；紅退場 |
| 3 | `≠`／`◐` 字形（`.offp-i`／`.part-i`，:1099） | 發生了什麼 | **拿掉 → slash 圈／pie 圈（都黃）** | 同色只差字形不好認；pie 多給了「做到幾成」 |
| 4 | 「實際 …」行 `.c-x`（:1100） | 實際做了什麼 | 月檢視 **hover**；週檢視／agenda **留** | 月檢視空間給不起；大檢視本來就是看細節的地方 |
| 5 | big 檢視的 `≠ 沒照課表 · …`／`◐ …` 說明行（:1101） | 為什麼 | **留**（週／agenda），文字改用 `compliance.status.*` 同一套字 | 跟 dialog 完成盒同源 |
| 6 | `⚠ note` 自動調整備註（`.adapt-note`，:1101、:426） | 系統為什麼動它 | **hover ＋ dialog**（`PlanMark.detail` 已經有） | 跟 ↻ 徽章重複表達；硬編碼色順便拿掉 |
| 7 | 「熱」標籤（`.heat-tag`，:58、:1099） | 是熱適應課 | **拿掉 → 類型 icon 用太陽** | 類型就是 icon 的事；`isHeat(s)` 判斷留著決定 icon |
| 8 | ↻／✎（`PlanMark.icon(s)`，右上角） | 誰改過 | **搬到 icon 方塊角落**（`PlanMark.icon(s, true)`，總覽已這樣） | 右上角只留手錶 |
| 9 | 手錶 icon 5 種（`syIcon`，:989-998、:270-272） | 手錶是不是最新 | **留右欄；只畫 outdated／failed** | 正常狀態不該佔注意力（Q2-1）；已推送／未推送寫 dialog |
| 10 | 暫定：虛線左邊（:263）＋ 6% 底色 | 會變 | **留 → 整圈虛線外框＋點線圓圈**，底色拿掉 | 一處就夠 |
| 11 | `.st-missed` 灰字劃線＋「未完成」文字（:260-262） | 沒做 | **劃線與文字都拿掉 → 灰字＋灰外框＋空圈**；字眼改「跳過」 | 劃線與 ✗ 都是失敗符號（SP-393）；過去的空圈就是跳過 |
| 12 | 中繼列 `.c-m`（家族 · 分鐘 · 距離 · TSS → 實際） | 多久多重 | **留**（手機月檢視 hover） | 第 1 頻率問題 |
| 13 | target 列（`s.target`，月檢視沒畫、hover 有） | 要怎麼跑 | **hover ＋ dialog**（不變） | |
| 14 | detail／walk_hint `.c-d`（週檢視，:1101） | 細節 | **留週檢視**（不變） | |
| 15 | title tooltip 全文（:1098） | 全部 | **留**，順序改成 §7.1 的順序，門檻不再附 | |
| 16 | 課表外活動 `.chip.act`：● 前綴＋類型色左邊（:265-267） | 沒排課的活動 | **留 → 點線外框＋類型 icon（活動分類→icon）**；● 拿掉 | `CAT_VAR` 顏色表退場，改成 `CAT_ICON`：road/trail→easy 心（或 session class 判 z3/z5/long）、hike→hike、bike→bike（新）、strength→槓鈴、walk→hike、other→圓點 |
| 17 | 交換中 `⇄`／`.swap-src`／`.swap-lock`（SP-359） | 操作狀態 | **留**（不變） | 暫時性，不是資訊層 |
| 18 | 休息拖曳卡 `.rest-mv` | 可拖的休息 | **留**，改成月亮 icon＋「休息」 | 跟新卡片同語言 |

### 8.2 日格與週列（:120-165、:300-308）

| 元素 | 處置 | 理由 |
|---|---|---|
| 週期左條 `.wk::before`（4px `--ph`） | **留** | 週層級、位置不跟卡衝突（卡的色條在卡內）；圖例寫明 |
| 週暫定斜紋 `.wk.prov .cell`（135°） | **拿掉** | 暫定已在每張卡的虛線外框；斜紋跟不排課 45° 斜紋疊在一起很難讀（:143 就是在處理這個衝突） |
| 週統計「暫定（重排時會依進度調整）」tag（:1164） | **留**（改短成「暫定」，說明進 `?`） | 週層級的一句話，給整週 |
| agenda `.ag-sum` 的「暫定」（:1257） | **拿掉** | 卡上已有 |
| 不排課 45° 斜紋＋`.bo-chip` | **留**（不變） | 跟課無關，是日的屬性 |
| 休息日淡綠底 `.rest-d`（`--s3` 9%） | **留**，顏色改用 `--good` 的 8%（`--s3` 退場後不留孤兒變數） | |
| 今天 `.today`／選取 `.sel`／範圍 `.rng`（`--accent`） | **留** | 操作狀態 |
| 過去日淡底＋未完成卡 75% 透明（:136） | **留淡底；透明拿掉** | 跳過的卡已是灰字＋灰外框＋空圈，不再疊 |
| `.cell.past .dnum` 灰字 | 留 | |
| 睡在高處 `.hn-tag`（SP-259，`--s4`） | 留，顏色改 `--line`（`--s4` 退場） | |
| 週統計 `compW` 完成度 % ＋ cp 顏色＋ tooltip 門檻（:1166） | **改成「完成 TSS 310」，不上色、不帶分母**（SP-393 §7 #9）；計畫 TSS 在 hover | 分母＝扣分結構 |

### 8.3 課表 dialog（`vsHtml` :1972-1992、`openDlg` :2040-2047）

| 元素 | 處置 |
|---|---|
| `✓ 已完成`（綠） | 改成**狀態一句話**：圓圈（同卡片）＋ `compliance.status.*` 文字＋ `pct`，例「◔ 部分 72% · 強度不足」「⊘ 換了課 · 排輕鬆跑，實際騎車」；完成盒標題不再固定寫 ✓ |
| `≠ 沒照課表`／`◐ …` 兩個 `?` | 併成一顆 `?`（圖例表） |
| `<dl class="vs">` 計畫／實際兩列 | 留；`dd.off` 維持 `--watch` |
| `compTip(cp)` 整句 | 改成「TSS 109% · 時間 96% · 強度 72%」三個數字，不重複狀態字 |
| 副標的「· 暫定」「· 已修改」「· 自訂」（:2040） | **留**（dialog 是看細節的地方）；但 ↻／✎ 的 `PlanMark.detail` 盒也在，`· 已修改` 與 ✎ 盒只留 ✎ 盒 |
| `⚠ note`（:2047） | **留**（搬來這裡的終點） |
| 手錶狀態 | 新增一行文字「手錶：已推送／需更新／失敗／未推送」（`syLabel`）——卡上不畫的三種在這裡看 |

### 8.4 總覽本週清單（`overview.html` `sessHtml` :646-664、CSS :114-129）

| 元素 | 處置 |
|---|---|
| `.ty-*` 類型色方塊（26px，白 icon） | **拿掉顏色 → 同課表的 22px 中性方塊**；共用元件 `sessChip()`（§10 ③） |
| `.ok` 角標 ✓（換色） | **拿掉 → 右欄圓圈**（同一套 `G.*`） |
| `≠`／`◐` 前綴 `.offp`（:663） | 拿掉 → 圓圈 |
| `.missed` 55% 透明＋劃線 | 透明與劃線都拿掉，灰字＋空圈 |
| `cp.pct` 尾綴「· 72%」（只在非綠） | 留，改成全部畫在圓圈 pie 裡，文字只留 hover |
| tooltip 門檻句（:656） | 拿掉門檻 |
| `.extra`（課表外活動，`--bar`） | 點線外框＋icon |
| `.mini.*`（:132-133，無人用） | 刪 |
| `not_pushed` 露 key（:659 `CB[...] \|\| s.coros.status`） | `CB.not_pushed = "COROS 未推送"`，並用 `provider_label` 取代寫死的 COROS |
| `week.short_aria` | 改用 `compliance.status.partial` |

## 9. 分級併成四種

### 9.1 規則（後端不改分級，只改顏色與字）

| 狀態（`compliance.status_of`） | 條件（不變） | 顏色（前端） | 圓圈 | 文字 key |
|---|---|---|---|---|
| `done` | 時間、TSS 皆 ±20% 且強度 ≥ 80%，類型對 | 綠 | check | `compliance.status.done` 完成 |
| `partial` | 時間或 TSS 偏 >20%；強度 50–80%；偏強；soft（跑反強度／換走跑項目但時間 TSS 綠） | 黃 | pie（填 `pct`） | `status.partial` 部分 |
| `off_plan` | 換走跑以外的運動；跑反強度且時間 TSS 不綠；`wrong_type` 非 soft | 黃 | slash | `status.off_plan` **換了課**（現「沒照課表」） |
| `missed` | 沒有對應活動 | 灰 | 空圈 | `status.missed` **跳過**（現「沒做」／「未完成」） |

- **後端 `level`（green/yellow/red）與 `with_plan_check` 不改**：它們留給教練視角的收合區與 `plan_history`。前端的顏色只看 `status`：done→`--good`、partial／off_plan→`--watch`、missed→`--faint`；**`level == "red"` 不再畫成紅色**。`LEVEL_LABEL` 四字改成「完成／部分／換了課／跳過」與前端一致。
- 紅色 pie（第一輪 Q2-6 原選 A）因 SP-393「紅退場」改為黃 pie：差多少由 pie 填滿比例講。

### 9.2 自動調整規則 E：拿掉「連續 2 堂紅」（使用者 2026-10-12 定）

規則 E（`adapt.py` `_fatigue` :502-540）有三個觸發：① TSB < −30、② CTL 爬升到 BLOCK 線、③ `_red_streak`（:492-499，連續 2 堂 `compliance.streak_red`）。③ 把「做太少」「換騎車」都當疲勞訊號，跟負荷無關、而且靠完成度顏色——使用者定：**拿掉 ③，規則 E 只看 ① ②（純負荷）**。「連續太強」要不要成為新觸發另開待決策單，不混進本單。

| 哪裡 | 改法 |
|---|---|
| `adapt.py` | 刪 `_red_streak`、`RED_STREAK`、`_fatigue` 的 `elif _red_streak(...)` 分支與「連續 2 堂偏離計畫（紅色）」文字；模組 docstring E 段同步 |
| `compliance.py` | 刪 `streak_red`（只有規則 E 在用） |
| `test_adapt.py` | 刪／改「兩堂紅觸發 E」的案例；新增一案：兩堂紅、TSB 正常 → **不**觸發 |
| `docs/spec`（adapt 規則 E） | 註記 2026-10 起 ③ 移除與理由 |
| 統計頁 | 歷史數字不變（達標率、完成率都看 status）；`.st-off_plan`／`.bg-off_plan` 維持 `--watch`，`.st-missed` 從 `--bad` 改 `--faint` |

### 9.3 文字對照表（i18n）

| 用途 | 現在 | 改成 | key |
|---|---|---|---|
| 四種狀態名 | 完成／部分／沒照課表／沒做（統計頁）；符合計畫／有點偏離／偏離計畫／未完成（`LEVEL_LABEL`） | **完成／部分／換了課／跳過**，前後端一致；en：Done／Partly／Different session／Skipped | `compliance.status.*`；`LEVEL_LABEL` |
| 課表 `vs.short_legend` | 強度或項目沒完全照課表（算部分完成） | 刪（併進 `status.partial`） | — |
| 課表 `vs.levels_tip`、統計 `kpi.ok_tip`、`kpi.rate_tip` | 三段各自寫一遍門檻 | 一份：`common.legend.levels`（§7 完成度區的四句），三處引用 | `common.legend.levels` |
| 總覽 `week.short_aria` | 強度或項目沒完全照課表（部分完成） | 刪 → `compliance.status.partial` | — |
| 課表 `legend.done_h`／`legend.watch_h`／`legend.*` 七個 | 圖例列 | 併進 `common.legend.*`；手錶四個只留 outdated／failed | `common.legend.*` |
| `compTip` 的「完成度 92%（符合計畫；TSS 109% · 時間 96%）」 | 狀態字＋數字 | 「92% · TSS 109% · 時間 96%」（狀態由圓圈與 status 字講） | 無 key（組字） |
| 課表 chip `.st-missed::before`「未完成」 | 紅字 | 刪 | — |
| adapt 規則 E 原因「連續 2 堂偏離計畫（紅色）」 | — | 隨 ③ 一起刪 | — |

其餘正向文案（完成率→完成 n 堂、連續達標→連續有練、安心句、亮點）在 `positive-compliance.md` §6，屬 SP-393 的實作單。

### 9.4 門檻單一來源

後端 `COMPLIANCE = {"green": .20, "yellow": .50}` 已經透過 calendar payload 的 `compliance_levels`（`plan_sessions.py:2935`）送到前端。前端三處硬寫（課表圖例 :1268、週統計 tooltip :1166、總覽 tooltip :656）改成只在 `common.legend.levels` 一處，用 `{green}`／`{yellow}` 參數帶入 `D.compliance_levels`；統計頁 `levels` 欄位已有（`dashboard()` 回傳 `levels`）。強度的 80%／50% 在 `plan_match.graded`——同樣加進 payload（`intensity_levels`），不再寫死在文字裡。

## 10. 遷移計畫

五步，每步可獨立上線、獨立驗收；順序是「先鋪 token 與元件 → 換卡 → 共用 → 後端 → 拆舊」。每步一張功能單，`parent` 指 SP-375。

### ① CSS token 與共用元件（`sesschip.js` 新檔）

做：`backend/static/sesschip.js`：`SessChip.icon(kind|category, {fam})` → 22px 方塊 HTML（含 ↻／✎ 徽章槽）；`SessChip.ring(status, pct, {prov})` → 圓圈 SVG（`G.*` 從 mockup 搬）；`SessChip.cls(s)` → `done cp-ok|part|off|miss fut prov act` class 字串。token：`--type`、`--cp`、`--tile-*` 不要（V1 不用）。加 `dashicons.js` 的 `bike`、`other`。
驗收：
- ✅ `tests/static/test_sesschip.js`（或 pytest 跑 node）：`ring("partial", 72)` 的 dasharray 等於 0.72 × 半圓周；`ring("done")` 含 check path；`cls` 對四種狀態回對的 class。
- 👆 mockup `schedule-chip-ui-mockup.html` 改成 `<script src>` 引 `sesschip.js` 後畫面不變。

### ② 課表 chip

做：`chipHtml` 改用 `SessChip`；CSS :225-272 換成 mockup `.chip` 那組；`.k-*`／`.fam-*`／`.cp-green|yellow|red`／`.offp-i`／`.part-i`／`.heat-tag`／`.adapt-note` 在 chip 內不再引用（先留 CSS，⑤ 刪）；`syIcon` 只在 outdated／failed 回 HTML；月檢視手機版（`@media (max-width: 600px)`）標題隱藏。aria-label 改 §7.1 順序。dialog `vsHtml` 改 §8.3。
驗收：
- 👆 課表頁月檢視：過去一週有完成、部分、換了課、跳過各一堂 → 四張卡分別是綠 check／黃 pie／黃 slash／灰空圈，前三張左側色條顏色一致、跳過的沒有色條也沒劃線，卡片本體白底，看不到類型顏色，**整頁沒有紅色**（除非有推送失敗）。
- 👆 未來一堂改過時間的課 → 卡片只有外框、右欄空圈＋黃手錶；再推送一次後手錶消失。
- 👆 把一堂課設暫定 → 虛線外框＋點線圈，週列沒有斜紋。
- 👆 手機寬度（<600px）月檢視：每張卡只剩方塊＋色條＋圓圈，hover／點開看得到標題。
- 👆 深色主題同上四條。
- 👆 點開一堂部分完成的課 → 完成盒第一行「◔ 部分 72% · 強度不足」，下面「TSS 109% · 時間 96% · 強度 72%」，只有一顆 `?`。
- ✅ 現有 `test_*` 不動（只改前端）。

### ③ 總覽本週清單共用

做：`sessHtml` 改用 `SessChip`；`.sx` CSS 改成 `.chip` 同款（26px→22px）；`.ok`、`.offp`、`.ty-*`、`.mini.*` 刪；`CB` 補 `not_pushed` 並用 `provider_label`；`week.short_aria` 改 key。
驗收：
- 👆 總覽本週與課表頁同一堂課：icon、色條顏色、圓圈形狀三者相同。
- 👆 一堂未推送的課 hover → 看到「COROS 未推送」不是 `not_pushed`。
- ✅ `test_overview.py` 現有案例過。

### ④ 字眼、統計頁顏色、規則 E

做：`LEVEL_LABEL` 與 `compliance.status.*` 改「完成／部分／換了課／跳過」（en 同步）；統計頁 `.st-missed`／`.bg-missed` 從 `--bad` 改 `--faint`，`.st-off_plan` 維持 `--watch`；adapt 規則 E 刪 `_red_streak`（§9.2），`compliance.streak_red` 一併刪；i18n 三處門檻改引 `common.legend.levels`。
驗收：
- ✅ `test_plan_match.py`／`test_compliance_dashboard.py` 不改期望值（分級沒動），只改 label 字串的 assert。
- ✅ `test_adapt.py`：原「兩堂紅觸發 E」案改成不觸發；TSB < −30 與 ramp BLOCK 的案照舊觸發。
- ✅ `grep -rn "streak_red\|_red_streak\|RED_STREAK" backend` 為 0。
- 👆 統計頁每堂課列表與類型堆疊條：看不到「沒做」「沒照課表」，跳過的列是灰字；整頁沒有紅色。
- 👆 課表頁 hover 一堂被規則 E 動過的課：原因只會是 TSB 或 CTL 爬升，不再出現「連續 2 堂偏離計畫」。

### ⑤ 拆舊與圖例收 `?`

做：刪 `.k-*`、`.fam-*`、`.cp-green|yellow|red`（chip 內）、`.offp-i`、`.part-i`、`.heat-tag`、`.adapt-note` 顏色、`.wk.prov` 斜紋、`.cell.past .chip` 透明、`CAT_VAR`、`.ty-*`、`--s1…s7` 在這兩頁的引用（其他頁的圖表仍用 `--s*`，不動）；`.k-notice` 的 `--k` 問題隨之消失；`renderLegend` 改成一顆 `?`；`legend.*` 七個 key 併 `common.legend.*`；`vs.short_legend`、`vs.levels_tip` 刪。
驗收：
- ✅ `grep -c "\-\-k\b\|\.k-\|\.ty-\|offp-i\|part-i\|heat-tag\|cp-red" schedule.html overview.html` 為 0。
- ✅ `backend/i18n` 的 baseline 檢查（`test_i18n*`）過：沒有孤兒 key、en 有對應。
- 👆 課表頁圖例列只剩「? 圖例」，點開是 §7 的表，淺／深色都讀得到。

### 順帶修的 bug（各在對應步驟）

| bug | 步驟 |
|---|---|
| `.k-notice` 沒定義 `--k`（:427） | ⑤（整組退場） |
| 總覽 `not_pushed` 露 key（:659） | ③ |
| `.adapt-note` 硬編碼 `#b36b00`（:426） | ②（搬進 dialog 時改 `--watch`） |
| `.mini.*` 死 CSS（overview :132-133） | ③ |
| `--accent` 與 `--s1` 數值相同 | ⑤（`--s1` 不再用在卡上後不再撞） |

## 11. 第二輪 Q2（2026-10-12 全部答完）

| # | 題 | 答 |
|---|---|---|
| 1 | 手錶 icon 只在需更新／失敗時畫 | 是 |
| 2 | 完成的課卡片白底、只有左側色條 | 是 |
| 3 | 「熱」→太陽 icon；⚠→hover＋dialog；課表外活動點線外框留 | 是 |
| 4 | 總覽本週清單共用同一套元件 | 是 |
| 5 | 統計頁註記分級調整 | 原答是；後因分級不改（§9.1）**不需要** |
| 6 | 紅色 pie | 原選 A 保留；SP-393 定「紅退場」後改為**黃 pie** |
| 7 | 手機月檢視只畫方塊＋色條＋圓圈 | 是 |
| 8 | 規則 E | **A：拿掉「連續 2 堂紅」觸發**，只看 TSB 與 CTL 爬升（§9.2） |
| 9 | 與 SP-393 的交集 | 等 SP-393 → 採其 §7 #1–5、#9：紅退場、跳過不劃線、字眼改、色條兩色、週統計去分母 |
