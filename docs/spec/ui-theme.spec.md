# Module Spec: ui-theme (外觀：Trig 配色與經典配色)

> **Last Updated**: 2026-10-29
> **Status**: Active（SP-974）
> **Domain Layer**: Supporting
> **Source**: SP-974（配色 mock A／B、Q1 的回答）；logo `backend/static/icons/trig-logo.svg`（SP-971）

## Overview

全站配色有四種，使用者在 設定 › 偏好設定 › **外觀** 的下拉選（SP-1011：偏好設定分頁在個人資料後面，放外觀和地圖；以前在個人資料。舊連結 `#appearance` 照 hash → 區塊 → 分頁打開新分頁，`TABS`，`backend/static/settings.html:3706-3708`）：

| 值（cookie `theme`） | 畫面 | `<html>` 屬性 |
|---|---|---|
| `trig-cream` | Trig 米白（mock B：米白底、山的青綠當主色、金色標比賽） | `data-palette="trig-cream" data-theme="light"` |
| `trig-badge` | Trig 深綠（mock A：徽章深青綠當主色和整條導覽列、金色當目前頁／今天／比賽） | `data-palette="trig-badge" data-theme="light"` |
| `classic` | 經典（跟隨系統）：各頁自己的淺色／暗色，跟 SP-974 之前一模一樣 | 沒有屬性 |
| `classic-dark` | 經典深色：各頁自己的暗色，一律暗 | `data-theme="dark"` |
| （沒選過） | 系統淺色 → Trig 米白；系統深色 → 經典深色；系統切換時跟著變 | 同上兩列 |

Trig 只有淺色版；選了 Trig 就一律亮（`data-theme="light"` 把每頁的暗色規則關掉）。

## 檔案

| 檔案 | 內容 |
|---|---|
| `backend/static/theme.css` | Trig 兩套的 token（`:root[data-palette="…"]`）、深綠的導覽列與訪客頁 `header.top`、金色重點（`.chip.race`、深綠的 `.cell.today .dnum`） |
| `backend/static/theme.js` | 讀 cookie、決定預設、設 `<html>` 屬性、`window.TrcTheme`（見下） |
| `backend/i18n/pages.py` `theme_head()` | 兩個檔去掉註解後 inline 寫進每一頁 `<head>` 的最前面（`render()`，在 i18n 之前、頁面自己的 `<style>` 之前），所以第一次繪製就是對的顏色、不會先閃一下原本的色；inline 是因為訪客的 `/share/*` 拿不到 `/api/v1/static`。render 快取的 stamp 含這兩個檔的 mtime |
| `backend/static/settings.html` `#appearance` | 外觀下拉（偏好設定分頁、地圖上面；SP-1011 以前在個人資料），改了立刻套用，不用按儲存；沒選過時下拉顯示正在用的那個（系統深色時顯示「經典深色」），底下一行說明預設規則 |
| `backend/static/i18n/*/shell.json` `theme.*` | 四個選項的名稱（含品牌字，只能放 shell，見 test_brand_sp971） |
| `backend/static/i18n/*/settings.json` `look.*` | 外觀區塊的文字 |

**存在哪**：這台瀏覽器的 cookie `theme`（1 年，`Path=/; SameSite=Lax`），跟語言的 `lang` cookie 同一種做法；不存帳號。理由：要在 `<head>` 同步決定（帳號設定要等 API）、未登入的頁面（首頁、登入、分享）也要套到、而且「系統深色時的預設」本來就只有瀏覽器知道。

## Token

「經典」（`classic`／`classic-dark`，SP-974 之前的原本配色）= 每一頁自己 `:root` 裡的色票（兩個家族：舊的 `--accent #2563eb`／暗底 `#0f1216`，新的 `--accent #2a78d6`／暗底 `#111110`），`theme.css` 不碰；所以「經典」跟 SP-974 之前完全一樣。Trig 兩套用 `:root[data-palette=…]`（specificity 0,2,0），蓋過頁面的 `:root`（0,1,0），跟順序無關；兩個家族的名稱都設：

| token | Trig 米白 | Trig 深綠 | 用途 |
|---|---|---|---|
| `--bg` | `#f3efe4` | `#eef2f1` | 頁底 |
| `--panel` | `#fcfbf7` | `#ffffff` | 卡片 |
| `--text` / `--ink` / `--c-line` / `--ac-ink` | `#1f2a28` | `#132629` | 內文 |
| `--muted` / `--info` / `--ac-ink2` | `#5a5a50` | `#4a5b5e` | 次要字 |
| `--faint` | `#8b897d` | `#7d8b8d` | 更淡的字 |
| `--line` | `#e2dccb` | `#d8e0df` | 線 |
| `--accent` / `--a` / `--c-run` / `--k-race` / `--cp-line` / `--iv-wp` | `#2f6b64` | `#1f4a52` | 主色 |
| `--soft` / `--accent-soft` / `--tip-bg` / `--code` | `#e2ece6` | `#e1ecea` | 選中的底 |
| `--on-accent` | `#ffffff` | `#ffffff` | 主色上的字 |
| `--ok` / `--good` | `#28712d` | `#1d7a3c` | |
| `--warn` / `--watch` | `#9c5208` | `#a35a00` | |
| `--err` / `--bad` | `#b3361f` | `#b42318` | |
| `--s1`…`--s7` | `#2f6b64 #d0632a #3b6fb6 #b88a00 #c45c86 #6d8f2a #7a55a8` | `#1f4a52 #d9622b #2a78d6 #c08c00 #cf5a8a #4f8a2b #6a4fb3` | 圖表系列色（青綠、橘、藍、金、粉、綠、紫） |
| `--c-fill` / `--ac-brush` | accent 10% | accent 10% | |
| `--k-long` | `#3b6fb6` | `#2a78d6` | |
| `--gold` / `--gold-ink` | `#f2c46d` / `#8a6410` | 同 | 金色重點／金色深字 |
| `--theme-color` | `#2f6b64` | `#1f4a52` | `<meta name="theme-color">`（原本：`#2563eb`） |
| `--nav-bg` / `--nav-text` / `--nav-muted` | — | `#1f4a52` / `#f3efe4` / `#b9cfcc` | 深綠的導覽列 |

每一組都過 WCAG AA：字 4.5、圖形 3（`test_theme_sp974.test_the_palettes_are_the_mocks_and_pass_aa`）。頁面自己的語意色（區間色階、週期色條、路面、溫度、`--warn-bg`、`--shadow`…）不換。

## `window.TrcTheme`（theme.js）

| | |
|---|---|
| `saved()` | cookie 裡的選擇，沒有是 `""` |
| `current()` | 生效的那個（預設已解開） |
| `set(name)` | 記住並套用；`""` 忘掉（回到預設）；發 `trc-theme` 事件 |
| `isDark()` | 頁面是不是暗的（`data-theme` 優先，再看系統） |
| `color(hex)` / `recolor(obj)` | 原本的圖表色換成 Trig 的同色相（只在 Trig 生效時）：`#2a78d6`／`#2563eb`→`--s3` 藍、`#eb6834`→`--s2` 橘、`#1baf7a`→`--s6` 綠、`#eda100`→`--s4` 金、`#e87ba4`→`--s5` 粉、`#8a8984`→`--faint`、`#52514e`→`--muted`；其他（心率紅 `#e34948`、`#dc2626`、區間色…）不換 |
| `themeColor()` | 瀏覽器列的顏色 |

同色相而不是「s1→s1」：`views/*.json` 的說明文字寫「藍線＝長期負荷」「綠 有效訓練」，換色後要還是藍、綠。

## 用到的地方

- `shell.js`：`<meta name="theme-color">` 用 `TrcTheme.themeColor()`；導覽列吃頁面 token（深綠另外在 theme.css 改成深底）。
- `charts.html`：`/views` 和每張圖的回應過 `TrcTheme.recolor`；`pzDark()` 原本就看 `data-theme`。`climb_vam.js`、`pole_compare.js`、`pd_trend.js` 的淺色兩色過 `TrcTheme.color`。
- 地圖軌跡：`css("--accent")`，跟主題走（`#2563eb` 只是讀不到時的備援）。
- 總覽、賽事計算機、課表統計、說明書：直接吃 `--s1…--s7`。

## 模式切換（`.modesw`，SP-974 Q2：mock D「分段軌道＋圖示方塊」）

圖表分析的「趨勢｜單次活動」、課表三頁（`schedule`／`compliance`／`templates`）的「日曆｜課表統計｜範本」。四頁各帶一份**一模一樣**的 CSS（`/* 模式切換 (SP-974 Q2` 開頭那段，測試檢查四份相同），只吃 token：

- 一條軌道把選項連成一組：底 `color-mix(--accent 7%, --bg)`、框 `color-mix(--accent 16%, --line)`。
- 每格左邊一個圖示方塊（`<span class="mi">` 包住原本的 svg）：平常淡主色底（11%）、主色圖示；標題 `--muted`。
- 滑過：格子半透明 panel 底、標題 `--text`、方塊 20%。
- 選中（`.on`／`aria-current="page"`）：格子浮起變 `--panel`＋細陰影與 22% 主色框，方塊填滿 `--accent`、圖示 `--panel`，標題 `--accent`。
- 手機（≤ 620 px）：軌道撐滿整列、各格平分，拿掉說明那一行、方塊縮成 26 px。

以前寫死的深橘（`#b4530f`／`#8f3f0a` 漸層）拿掉了；四套外觀都用各自的主色。

## Known limits
- `views/*.json` 裡沒對應的寫死色（紅、`#16a34a`、`#f59e0b`、紫、灰藍等約 80 個）不跟主題變。
- `manifest.webmanifest` 的 `theme_color` 是全站一個值，不跟個人主題變。
- 換主題時，已經畫好的圖表不會重畫（重新整理才換色）；設定頁本身立即換色。

## Decisions Log

| 日期 | 決定 | 來源 |
|---|---|---|
| 2026-10-28 | A 徽章深綠、B 米白暖底兩套都做，一個「外觀」下拉：Trig 米白／Trig 深綠／原本（跟隨系統）／原本暗色 | SP-974 Q1-1、Q1-2 |
| 2026-10-28 | 沒設定的人：系統淺色用 Trig 米白、系統深色用原本暗色 | SP-974 Q1-3（照建議 (b)）；預設哪一套 Trig 是 AI 的決定 |
| 2026-10-28 | 存這台瀏覽器的 cookie，不存帳號 | SP-974 AI 的決定 |
| 2026-10-28 | 模式切換用 mock D（分段軌道＋圖示方塊），跟主題色、不再寫死深橘 | SP-974 Q2 |

## Change History

| 日期 | 單 | 內容 |
|---|---|---|
| 2026-10-28 | SP-974 | 新增：theme.css／theme.js、inline 注入、設定頁外觀、圖表色同色相對應；模式切換改成 mock D |
| 2026-10-28 | 下拉的「原本（跟隨系統）／原本暗色」改名「經典（跟隨系統）／經典深色」（英文 Classic），值（classic／classic-dark）不變 | 使用者：「原本暗色」名稱怪 |
| 2026-10-29 | code-sync（SP-1011, SP-974） | 外觀在設定的「偏好設定」分頁（SP-1011，`#appearance` 舊連結開新分頁）；沒選過的系統深色與「原本」色票的名稱跟著下拉改叫「經典」／「經典深色」。測試：`backend/tests/e2e/test_settings_prefs_tab_sp1011_e2e.py`、`backend/tests/test_theme_sp974.py` |
