# Module Spec: ai-qa (AI 問答：問 AI、給 AI 的摘要、規則引用)

> **Last Updated**: 2026-10-21
> **Status**: Active（後端 SP-785、前端 SP-786；拍板在 SP-306 Q1／Q2 方案 X）
> **Domain Layer**: Supporting
> **Source**: SP-306 `## 結論`（入口方案 X、先只給擁有者、只送彙總、舊 `ai_api_key` 一起處理）

## Overview

使用者在任何一頁按「問 AI」，問正在看的那張卡／圖／課／計算結果，或整頁。模型只當「查 app 自己的規則與數字」的介面：回答每一段標出是 **App 規則**（附出處）還是 **AI 推論**，不自己發明訓練建議；醫療相關一律提醒就醫。

| 部分 | 檔案 |
|---|---|
| 設定、金鑰、次數 | `backend/ai/config.py` |
| 送出的內容（彙總、清理） | `backend/ai/context.py` |
| prompt、呼叫、解析、快取 | `backend/ai/qa.py` |
| 醫療字詞 | `backend/ai/medical.py` |
| 費用記錄（給 Grafana） | `backend/ai/spend.py` |
| API | `backend/api/ai.py`（`/api/v1/ai/*`） |
| 按鈕與對話框 | `backend/static/askai.js`（由 `shell.js` 載入） |
| 設定頁 | `settings.html` `#askai`、`backend/static/ai_settings.js` |

## 誰能用

- **只有站主**（SP-306 Q1-1 C）：`config.allowed()` ＝ `tenancy.is_owner()` 且有 `ai.owner_key`（`tenancy.OWNER_ONLY_CAPS`）且不是示範模式。其他人每個 `/api/v1/ai/*` 回 403 `AI_OWNER_ONLY`；示範模式不掛這個 router（`main.py` `owner_only`）；教練代看學員時是學員的 tenant，一樣 403，`/api/v1/session` 也不給 `ai`。
- 之後商業版「使用者自己的 key」（Q1-1 A）還沒做。

## 金鑰

讀取順序：

1. 環境變數 `TRC_ANTHROPIC_API_KEY`，或 `TRC_ANTHROPIC_API_KEY_FILE` 指的檔案（Docker secret）。列在 `settings/env_secrets.NAMES`，所以 `applog.redact` 會遮掉。
2. 設定頁貼上的 key：`secrets.seal()`（伺服器的 Fernet 金鑰）加密後存在 `<owner home>/ai.json` 的 `api_key`（權限 600）。任何 API 都不回傳，只回 `masked`（`…` ＋ 最後 4 碼）；`env_secrets._extra()` 把它的值也加進 log 遮罩；`secrets.ciphertext_exists()` 看得到它（不會在它還在時產生新的伺服器金鑰）。

舊的 `athlete_settings.ai_api_key`（SP-48）不讀：`init_db` 會把值清掉並刪欄（`db/database.RETIRED_COLUMNS`）。

其他設定：`TRC_AI_MODEL`（預設 `claude-sonnet-5-5`，只收英數與 `-._:`）、`TRC_AI_DAILY_LIMIT`（預設 30，1～1000）。開關在 `ai.json` 的 `enabled`（預設開）。

## 送出什麼（只有彙總）

```
{"app":     {today, phase {label, goal, start, end}, headline,
             rules: [{id, title, level, value, judgment, why, action, source}],   # 狀態卡每個指標，不含 spark／extra
             todo:  [{id, title, detail, source}],                                # 待辦前 8 項
             weeks: [{week, hours, km, climb_m, load_tss, sessions, days}]},      # 近 8 週合計
 "in_view": 給 AI 的摘要（下一節），整頁時 kind = page}
```

每個 rule／todo／卡片規則／卡片出處依序編號 `S1, S2…`，模型引用這些 id。

### 給 AI 的摘要（前端填、後端再清一次）

```
{"kind": "card" | "chart" | "session" | "calc" | "hint" | "page",
 "page": "<shell 的頁面 id>",
 "title": "心率與功率圖（10/18 越野跑）",
 "summary": "卡片上看得到的文字",
 "facts":   [{"label": "CTL", "value": "52"}],
 "rules":   [{"text": "低強度比例 < 80%：偏少", "source": "Seiler 2010"}],
 "sources": [{"title": "手冊：負荷平衡", "url": "/manual/charts#form"}]}
```

- 前端預設從 DOM 讀：卡片標題、看得到的文字、卡內 `?` 的手冊連結（`data-manual`）。卡片可以用 `data-ai-card`／`data-ai-kind`／`data-ai-title`、`data-ai-facts`／`data-ai-rules`（JSON）說得更清楚。
- 讀卡片前，`askai.js` 先對那張卡發一個會冒泡的 `askai:describe` 事件（SP-884），頁面在這時用自己**當下**的資料填 `data-ai-*`（不用每張卡先塞 JSON）：
  - 賽事計算機（`racepower.html` `aiCalc`）：`#results` 送整場數字（距離、爬升／下降、移動時間、含停留、ETA、平均功率／% CP、配速、平路等效配速、努力度、環境係數、目標對模型、算法）＋分段（每段：類型、坡度、↑↓、走／跑、配速、功率帶或參考、心率上限、分段時間、累計、ETA、溫度、夜間）；規則是結果帶的提示（伺服器的 warnings、下雨、冷／夜間、目標比模型快、登山杖）。`#profile-sec` 只送分段。分段太多放不下 40 項時，送最花時間的那幾段、照路線順序。
  - 課表課卡（`schedule.html` `aiSession`）：日期、類型、狀態、計畫分鐘／距離／爬升／負荷、實際跑了什麼與負荷、完成度數字、目標各項、存著的結構（每一步）、怎麼練；規則是完成度判斷（出處是圖例裡定義它的那一行，含門檻）、規則 D 的太強提醒、上坡改走、自動調整的理由、目標的依據。對話框開課表時才推導、沒存的結構不送（目標與說明已經帶到）。
  - facts 的 label 是給模型看的英文、畫面不顯示；值用頁面自己的字。
- 「最後點過的卡」被頁面重畫掉時（課表關掉課的對話框會重畫），用同一個 `data-uid`／`id` 找回重畫後的那張。
- 後端 `context.clean_focus`：不認得的欄位丟掉；每個字串有長度上限（摘要 2500 字、整份 12 KB）；兩個小數經緯度並排（`24.12345, 121.12345`）換成 `[location removed]`；連續 40 個以上的數字（串流、軌跡）換成 `[numbers removed]`；出處網址只收 `/manual/<章>#<節>`。所以不管頁面放了什麼，軌跡和位置都出不去。
- `POST /api/v1/ai/context` 回傳一個問題會送出的完整內容（不呼叫模型），驗證用。

## 回答

模型被要求只回 JSON：`{"parts": [{"type": "rule" | "inference", "text", "refs": ["S1"]}], "medical": bool}`。`qa.parse_answer`：

- `rule` 段一定要引用有送出去的 id，否則改成 `inference`；
- 不是這個 JSON 的回答整段當一個 `inference`；
- `sources` ＝ 有被引用的 id 的 `{id, kind, title, source, url}`。

`medical`：問題含醫療字詞（`medical.is_medical`，中英）或模型自己標，對話框就顯示固定的「請就醫」一行。

`POST /api/v1/ai/ask` 回 `{parts, sources, medical, cached, used_today, daily_limit, model, sent}`；`sent` 是送了什麼的計數（不含值），對話框底下顯示「送出的只有彙總：12 項狀態規則、近 8 週訓練量、『…』的文字」。

## 成本：快取與每日上限

- 快取：同一個人、同一個模型與語言、同一個問題、同一份內容（含對話前文）6 小時內直接回記憶體裡的答案，不算次數；換 key 會清快取。
- 每次真的呼叫模型算一次（`<owner home>/ai_usage.json`，依伺服器日期）；到上限回 429 `AI_DAILY_LIMIT`。失敗的呼叫不算。一次只呼叫一個。
- log 只記狀態碼、毫秒、token 數，不記 key、問題、回答。
- 費用進 NAS 共用的「💸 API 使用量」Grafana（SP-882，`backend/ai/spend.py`）：每次真的呼叫模型（快取的答案不算），在 `<資料夾>/logs/ops/api_calls.lp` 加一行 InfluxDB line protocol——`api_call,provider=anthropic,model=…,project=trailruncoach,purpose=ask,status=success|error input_tokens=…i,output_tokens=…i,cost_usd=…,latency_ms=…i,error_message="<錯誤碼>"`。USD 用 `spend.PRICE` 當場算（不在表上的模型記 0）。App 不拿 InfluxDB token、不對外寫：`deploy/ops-monitoring` 的 Telegraf `inputs.tail` 讀這個檔，寫進 bucket `feeder`（SP-388 的慣例）。檔案超過 2 MB 換成 `.1`。寫不進去（log 檔關掉、磁碟滿）只記一次 warning，不影響回答。

## 前端（SP-786，方案 X）

- 全站一顆「問 AI」：桌機右下、手機在底部導覽列上方右側；建議框在時疊在它上面。`TRC_SESSION.ai.on`（站主且開著）才載入 `askai.js`。
- 打開時帶入「最後點過或展開過的卡」，沒有就用畫面中央那張；頂端「正在問：<標題>（<頁名>）」，✕ 改問整頁。換了對象就清掉對話。
- 預設問題依 kind：整頁 4 個、卡／圖／課／計算機／說明各 3 個（`shell.json` `askai.preset.<kind>.<n>`）；也可以自己打字（Enter 送出）。
- 每個 `?` 說明框（`ManualLink.decorate`）和提示的「詳細」清單（`ul.hint-detail`）最下面一行「還不懂？問 AI」，帶入那張卡、kind = hint、摘要 ＝ 說明文字＋卡片文字。
- 沒設 key 時對話框上方說明去哪設；錯誤碼對到 `askai.err.*`。
- 設定 › 進階設定 › AI 問答：開關、key 狀態（環境變數／已設定 …末四碼／未設定）、貼上／清除、模型與今天次數。

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/api/v1/ai/ask` | `{question, focus?, history?}` |
| POST | `/api/v1/ai/context` | `{focus?}` → `{context, refs}` |
| GET | `/api/v1/ai/settings` | `{enabled, key {source, masked}, model, daily_limit, used_today}` |
| PUT | `/api/v1/ai/settings` | `{enabled?, api_key?}`（`""` 清除） |

錯誤 `{detail: {code}}`：`AI_OWNER_ONLY` 403、`AI_OFF`／`AI_NO_KEY` 409、`AI_EMPTY`／`BAD_KEY` 400、`AI_DAILY_LIMIT` 429、`AI_BUSY` 503、`AI_KEY_INVALID`／`AI_UPSTREAM` 502。

## Known limits

- 資料來源條款：Strava 明文禁止把資料送進 AI（`docs/research/data-hubs.md` §3.1）；COROS 條款還沒查。目前只有站主用自己的資料；開放給其他人前要先查、必要時排除來源。
- 「正在看哪一塊」是 DOM 猜的（最近的 `.card`／`section`／`.panel`…）；猜錯時頂端看得到、可以改成整頁。結構化的數字目前只有賽事計算機的結果與分段、課表課卡（SP-884）；其他卡送看得到的文字。
- 快取在記憶體，重啟就沒了；次數依伺服器日期。

## Change History

| Date | Source | Ticket | Summary |
|------|--------|--------|---------|
| 2026-10-20 | feature | SP-785 | 新增：`backend/ai`（config 金鑰與開關、context 彙總與清理、qa prompt／呼叫／解析／快取／每日上限、medical）、`/api/v1/ai/*`、`TRC_ANTHROPIC_API_KEY`（或 `_FILE`）或設定頁加密存放、log 遮罩、`/api/v1/session` 的 `ai`。測試：`backend/tests/test_ai_qa_sp785.py` |
| 2026-10-21 | maintenance | SP-885 | 英文介面巡一次：「正在問」對課表課卡寫課名與日期（`data-ai-title`，不再是卡片整串 aria-label）、對計算機結果／分段卡用頁面語言的標題（結果區本身的標題還沒翻）。測試：`backend/tests/e2e/test_askai_en_sp885_e2e.py` |
| 2026-10-21 | feature | SP-884 | 新增：`askai.js` 讀卡前發 `askai:describe`，賽事計算機（`#results`／`#profile-sec`）與課表課卡在那時填 `data-ai-facts`／`data-ai-rules`（整場數字＋分段、課的數字＋結構、提示與完成度判斷）；最後點過的卡被重畫時用 `data-uid`／`id` 找回。測試：`backend/tests/e2e/test_askai_facts_sp884_e2e.py` |
| 2026-10-21 | feature | SP-882 | 新增：`backend/ai/spend.py`，每次模型呼叫的 token 與 USD 寫成 `logs/ops/api_calls.lp`（共用 `api_call` schema），`deploy/ops-monitoring/telegraf.conf` 加 `inputs.tail` 與只收 `api_call` 的 `feeder` output。測試：`backend/tests/test_ai_spend_sp882.py` |
| 2026-10-20 | feature | SP-786 | 新增：`static/askai.js`（全站一顆問 AI、帶入正在看的卡、預設問題、App 規則／AI 推論標示、「還不懂？問 AI」）、`shell.js` 載入與 `?` 說明框掛點、設定頁 `#askai`＋`ai_settings.js`、`shell.json`／`settings.json` 中英文字。測試：`backend/tests/e2e/test_askai_sp786_e2e.py` |
