# Module Spec: ai-qa (AI 問答：問 AI、給 AI 的摘要、規則引用)

> **Last Updated**: 2026-10-29
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
| 來源（本機模型／Anthropic／Gemini）、照順序換下一個 | `backend/ai/providers.py`（SP-997） |
| 醫療字詞 | `backend/ai/medical.py` |
| 費用記錄（給 Grafana） | `backend/ai/spend.py` |
| API | `backend/api/ai.py`（`/api/v1/ai/*`） |
| 按鈕與對話框 | `backend/static/askai.js`（由 `shell.js` 載入） |
| 設定頁 | `settings.html` `#askai`、`backend/static/ai_settings.js` |

## 誰能用

- **站主**（SP-306 Q1-1 C）：`tenancy.is_owner()` 且有 `ai.owner_key`（`tenancy.OWNER_ONLY_CAPS`）且不是示範模式；用站主的 key。
- **登入的會員，用自己的 key**（SP-883；SP-306 Q1-1 A）：伺服器環境變數 `TRC_AI_MEMBER_KEYS`（`1`／`true`／`yes`／`on`）開著時，`USER` tenant 而且有 `ai` cap（`USER_CAPS` 都有）就可以用——免費、訂閱都可以（SP-883 Q1-1 A）。開關**預設關**（SP-883 Q1-2 A：等 SP-881 查完 COROS／Garmin 條款能不能把資料送給模型）；關著時會員跟以前一樣全部 403、看不到「問 AI」。
- `config.allowed()` ＝ 上面兩種之一（`config.is_member()` 是第二種）。其他人每個 `/api/v1/ai/*` 回 403 `AI_OWNER_ONLY`；示範模式不掛這個 router（`main.py` `owner_only`）；教練代看學員時中介層 `accounts/viewing.check` 不放行 `/api/v1/ai/*`（不在 `READ_OPEN`／`CALC`／`WRITE_OPEN`），`/api/v1/session` 也不給 `ai`。

## 金鑰

讀取順序：

1. **只限站主**：環境變數 `TRC_ANTHROPIC_API_KEY`，或 `TRC_ANTHROPIC_API_KEY_FILE` 指的檔案（Docker secret）。列在 `settings/env_secrets.NAMES`，所以 `applog.redact` 會遮掉。會員**永遠拿不到**這把（那是站主的錢）。
2. 設定頁貼上的 key：`secrets.seal()`（伺服器的 Fernet 金鑰）加密後存在**自己資料夾**的 `ai.json` 的 `api_key`（權限 600）——站主是 `<owner home>/ai.json`，會員是 `users/<id>/ai.json`。任何 API 都不回傳，只回 `masked`（`…` ＋ 最後 4 碼）；`env_secrets._extra()` → `config.redact_values()` 把站主的 key 與**這個請求解密過的會員 key** 加進 log 遮罩——會員的 key 在 `_saved_key()` 解密的那一刻記進 request 範圍的 contextvar，請求結束就沒了；寫 log 時**不讀任何租戶或會員資料夾**（公開頁的請求不碰租戶，`test_intro_sp637`）；`secrets.ciphertext_exists()` 看得到站主的（會員的 key 只存在多人模式，那時伺服器金鑰一定來自環境變數，不會自己產生新的）。
3. `data_registry`：`ai.json` 是 SECRET（不進「下載我的資料」、不進備份），`ai_usage.json` 是 DERIVED。刪帳號時整個使用者資料夾刪掉，key 跟著刪。

舊的 `athlete_settings.ai_api_key`（SP-48）不讀：`init_db` 會把值清掉並刪欄（`db/database.RETIRED_COLUMNS`）。

其他設定：`TRC_AI_MODEL`（預設 `claude-sonnet-5-5`，只收英數與 `-._:`）、`TRC_AI_DAILY_LIMIT`（預設 30，1～1000；站主與每位會員各自算）、`TRC_AI_MEMBER_KEYS`（預設關，見「誰能用」）。開關在各自 `ai.json` 的 `enabled`：站主與會員都預設開——會員還沒存 key 也看得到「問 AI」，對話框先請他填 key（使用者 2026-10-28：「填完 API key 就能用，還沒填就在對話框裡提醒」）。

**測試 key**（SP-883）：設定頁「測試」→ `POST /api/v1/ai/settings/test` → `qa.check_key()`：用現在這把 key `GET https://api.anthropic.com/v1/models?limit=1`——列模型，不吃 token、不計費、不算每日問題次數。每人每天最多 `config.KEY_TESTS_PER_DAY`（10）次，超過回 429 `AI_TEST_LIMIT` 且不打外部（不讓站變成驗證別人 key 的工具）。只看得出 key 有沒有被接受，看不出帳號有沒有餘額；餘額不足要到真的問時才知道（Messages API 回 400「credit balance is too low」→ `AI_NO_CREDIT` 402，不算次數）。

## 來源與順序（SP-997）

站主可以設三種來源，問的時候照順序試，前一個失敗就換下一個（`backend/ai/providers.py`）：

| 來源 | 怎麼呼叫 | 設定 |
|---|---|---|
| `local` 本機模型（例：站主 Mac 上 Ollama 的 `qwen2.5:32b`） | OpenAI 相容 `POST <base>/v1/chat/completions`（system 放第一則訊息、`stream: false`）；Qwen 的 `<think>…</think>` 先拿掉 | `ai.json` 的 `local {url, model}`；`url` 收 `http(s)://主機:埠`，自動補成 `…/v1`；不收帳號密碼、query。端點要 key 時可以給，加密存 `local_key`、送 `Authorization: Bearer`（Ollama 不用） |
| `anthropic` | Messages API（同 SP-785） | 同上「金鑰」 |
| `gemini` | Gemini 自己的 REST：`POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`，key 放 `x-goog-api-key` 標頭（不放網址）；`systemInstruction`、`responseMimeType: application/json`、`maxOutputTokens` 4096（Gemini 的「思考」也算在裡面）；`thought` 的段落不算回答 | 環境變數 `TRC_GEMINI_API_KEY`（或 `_FILE`），沒有就設定頁貼上、加密存 `gemini_key`；模型 `TRC_GEMINI_MODEL`（預設 `gemini-2.5-flash`） |

- **順序**：`ai.json` 的 `order`，預設 `local → anthropic → gemini`（使用者 2026-10-28：本機模型優先）。只列有設好的來源；只有 Anthropic key 時跟以前完全一樣。
- **換下一個的條件**：這個來源的任何失敗——連不上（Mac 睡眠、關機）`AI_UNREACHABLE`、逾時、key 被拒 `AI_KEY_INVALID`、餘額不足 `AI_NO_CREDIT`、沒有這個模型 `AI_NO_MODEL`、忙 `AI_BUSY`、回的不是 API 的 JSON、回空的（後面還有來源時）。全部失敗就回最後一個的錯誤。
- **本機模型的逾時**：連線 3 秒（`LOCAL_CONNECT_S`，`qa._post` 先試著連，連不上就換下一個）、讀 180 秒（筆電上的模型寫得比 API 慢）。
- **次數**：本機模型的回答**不算**每日次數；到上限後只剩本機模型會回答（付費的來源跳過），本機也失敗就回本機的錯誤（例如 `AI_UNREACHABLE`）；沒有本機模型時照舊 429 `AI_DAILY_LIMIT`。
- **回答多兩欄**：`provider`（哪個來源回答的）、`tried`（之前失敗的 `[{kind, model, code}]`）；`model` 是實際回答的模型。
- **費用線**：每次呼叫（含失敗的）照舊一行，`provider` 是 `anthropic`／`google`／`local`；本機模型 `cost_usd` 一律 0；Gemini 的價格還沒進 `spend.PRICE`（記 0）。
- **誰能設**：本機模型與順序只有站主（SP-997 Q1-1：本地模型只有管理員能用）；會員送 `local`／`order` 回 403 `AI_OWNER_ONLY`（整個請求什麼都不寫）。會員可以填**自己的** Gemini key（Q2-2，跟 SP-883 的 Claude key 同一套：存在自己的 `ai.json` 的 `gemini_key`、加密、解密時記進這個請求的 log 遮罩、只回末四碼、拿不到站主的 `TRC_GEMINI_API_KEY`）；會員的順序固定是自己填了的 key：Claude，再 Gemini，`purpose=ask_own_key`。
- **本機模型的上下文**：程式端不送 `num_ctx`（Ollama 的 OpenAI 相容端點不收每次的 `num_ctx`），靠 Mac 上 Ollama 的設定（使用者 Q2-3：開到 16K，`OLLAMA_CONTEXT_LENGTH=16384`）。
- **測試**：`POST /api/v1/ai/settings/test {kind}`，都是不吃 token 的 GET：Anthropic 列模型、Gemini 讀 `models/<model>`、本機 `GET <base>/models` 而且清單裡要有設定的模型（沒有回 `AI_NO_MODEL`）。

本機模型和 Claude 的差別（問答流程只用 system prompt＋對話＋回一段 JSON，沒用工具呼叫、prompt cache、串流，所以介面上沒有缺的）：

- **上下文長度**：一題送出去的內容（規則、待辦、8 週訓練量、正在看的卡，卡片最多 12 KB）加 system prompt 大約幾千 token，中文多時上萬。Ollama 的預設上下文很短（幾千 token），超過時從前面默默截掉——system prompt 先不見，回答就不照規則。Mac 上要把上下文開大（例如 `OLLAMA_CONTEXT_LENGTH=16384`，或 Modelfile 的 `num_ctx`）；OpenAI 相容端點不能每次帶 `num_ctx`。
- **只回 JSON**：靠 prompt 要求；模型沒照做時 `parse_answer` 整段當 AI 推論，引用不到的「規則」降成推論，所以不會冒出假的 App 規則，只是標示變少。
- **速度**：32B 在 Mac 上一題可能要幾十秒；對話框會等（讀 180 秒）。
- **傳輸**：區網／Tailscale 上是 http；Tailscale 本身加密，區網 IP 直連是明文（只有彙總，沒有軌跡）。

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
- 出處的網址只留在 refs 給對話框做連結，送給模型的 context 拿掉 `url`；system prompt 也要求文字裡不寫網址、路徑（SP-1023：本機模型把手冊路徑寫進回答）。
- 回答的語言照這個請求的介面語言（`?lang`、語言 cookie、帳號設定、瀏覽器語言，`request_context.locale_for_scope`），快取也分語言。繁中的指示明寫「台灣繁體中文、不用簡體字」（SP-1023：qwen 會寫成簡體）。
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
- 每次真的呼叫模型算一次，記在**問的人自己的**資料夾（`ai_usage.json`，依伺服器日期；同一個檔也記當天「測試」幾次）；到上限回 429 `AI_DAILY_LIMIT`。失敗的呼叫不算。每個人同時只呼叫一個（鎖依 tenant 分開，會員之間不互等）。
- 會員用自己的 key：費用記在他自己的 Anthropic 帳號，站方 0 元。
- log 只記狀態碼、毫秒、token 數，不記 key、問題、回答。
- 費用進 NAS 共用的「💸 API 使用量」Grafana（SP-882，`backend/ai/spend.py`）：每次真的呼叫模型（快取的答案不算），在 `<資料夾>/logs/ops/api_calls.lp` 加一行 InfluxDB line protocol——`api_call,provider=anthropic,model=…,project=trailruncoach,purpose=ask,status=success|error input_tokens=…i,output_tokens=…i,cost_usd=…,latency_ms=…i,error_message="<錯誤碼>"`。USD 用 `spend.PRICE` 當場算（不在表上的模型記 0）。App 不拿 InfluxDB token、不對外寫：`deploy/ops-monitoring` 的 Telegraf `inputs.tail` 讀這個檔，寫進 bucket `feeder`（SP-388 的慣例）。檔案超過 2 MB 換成 `.1`。寫不進去（log 檔關掉、磁碟滿）只記一次 warning，不影響回答。會員用自己的 key 的呼叫 `purpose=ask_own_key`（SP-883），Grafana 分得出站主花的與會員自付的；「測試」不計費，不記。

## 前端（SP-786，方案 X）

- 全站一顆「問 AI」：桌機右下、手機在底部導覽列上方右側；建議框在時疊在它上面。`TRC_SESSION.ai.on`（可以用的人且開著）才載入 `askai.js`。
- 打開時帶入「最後點過或展開過的卡」，沒有就用畫面中央那張；頂端「正在問：<標題>（<頁名>）」，✕ 改問整頁。換了對象就清掉對話。
- 預設問題依 kind：整頁 4 個、卡／圖／課／計算機／說明各 3 個（`shell.json` `askai.preset.<kind>.<n>`）；也可以自己打字（Enter 送出）。
- 每個 `?` 說明框（`ManualLink.decorate`）和提示的「詳細」清單（`ul.hint-detail`）最下面一行「還不懂？問 AI」，帶入那張卡、kind = hint、摘要 ＝ 說明文字＋卡片文字。
- 沒設 key 時對話框上方說明去哪設；錯誤碼對到 `askai.err.*`。
- **會員還沒存 key**（`TRC_SESSION.ai` 是 `{on: true, key: false, own: true}`，SP-883）：對話框不列預設問題，改成一段提醒（「先設定你自己的 API key 才能問」：用自己的 Anthropic key、費用記在自己的帳號）＋「到設定填 API key →」連到 `/api/v1/app/settings#ai-key-row`（設定頁把那一列加亮框 `link-asked`、捲到中間、游標放進欄位）；輸入框與送出都停用，`AskAI.ask()` 也不送。打開對話框時再問一次 `GET /api/v1/ai/settings`（別的分頁存好了就直接解鎖）；設定頁存好 key 會發 `askai:key` 事件，同一頁開著的對話框當場解鎖。站主沒 key 時照舊（上方一行說明，可以送、回 `AI_NO_KEY`）。
- 設定 › 進階設定 › AI 問答（SP-997 版面 mock A）：開關，然後**每個來源一張卡**，照試的順序排（`GET` 的 `sources`）：左邊序號、名稱與 `?`、副標（本機：模型 · 網址；Claude／Gemini：模型 · 環境變數／已設定 …末四碼／未設定）、右邊 ↑↓（只有站主、兩個以上來源時；按了就 `PUT {order}`）。有設定的卡：「測試」（`POST /settings/test {kind}`，結果寫在卡裡，✓ 綠／紅）、「換 key」或「編輯」、「清除」；沒設定的卡是虛線、序號變灰、欄位直接打開（本機：網址、模型名稱、選填 key；Claude／Gemini：貼上 key）。環境變數的 key 沒有換／清除。最下面「今天用了 n／30 次」，有本機模型時加「（本機模型不算）」。站主多一行順序說明；會員（`who: member`）只有自己的 Claude、Gemini 兩張卡、沒有 ↑↓，多一行「用你自己的 API key：Claude 或 Gemini……費用記在你自己的帳號」，`?` 換成會員版。Claude 卡沿用 SP-785／883 的 id（`#ai-key-row`、`#ai-key`、`#ai-key-state`、`#ai-key-test`…）。舊的伺服器回應沒有 `sources` 時只顯示 Claude 卡。
- 回答之前有來源失敗時（`tried` 不是空的），回答上方一行「本機模型連不上，改用 Claude 回答」（`[data-askai-via]`；原因照錯誤碼：連不上、key 無效、餘額不足、沒有這個模型、忙線中、沒有回應）。全部失敗而最後一個是本機模型時，錯誤是 `AI_UNREACHABLE`／`AI_NO_MODEL` 自己的說明。會員沒 key 的提醒改成「Claude 或 Gemini」；`/api/v1/session` 的 `ai.key` 是「有任何一個來源設好」。

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/api/v1/ai/ask` | `{question, focus?, history?}` |
| POST | `/api/v1/ai/context` | `{focus?}` → `{context, refs}` |
| GET | `/api/v1/ai/settings` | `{enabled, key {source, masked}, model, daily_limit, used_today, who, sources}`（`who`：`owner`／`member`；`sources` 照試的順序，每個 `{kind, ready, …}`，SP-997） |
| PUT | `/api/v1/ai/settings` | `{enabled?, api_key?, gemini_key?, local? {url, model, api_key?} \| null, order?}`（`""`／`null` 清除；後三個只限站主，SP-997） |
| POST | `/api/v1/ai/settings/test` | `{kind?}` → `{ok: true}`：那個來源可以用（SP-883、SP-997；沒給 `kind` 是 Anthropic key；不計費） |

錯誤 `{detail: {code}}`：`AI_OWNER_ONLY` 403、`AI_OFF`／`AI_NO_KEY` 409、`AI_EMPTY`／`BAD_KEY`／`BAD_LOCAL`／`BAD_ORDER`／`BAD_REQUEST` 400、`AI_NO_CREDIT` 402、`AI_DAILY_LIMIT`／`AI_TEST_LIMIT` 429、`AI_BUSY` 503、`AI_KEY_INVALID`／`AI_NO_MODEL`／`AI_UNREACHABLE`／`AI_UPSTREAM` 502。

## Known limits

- 資料來源條款：Strava 明文禁止把資料送進 AI（`docs/research/data-hubs.md` §3.1）；COROS 條款還沒查。會員用自己 key 的程式已備好（SP-883），但 `TRC_AI_MEMBER_KEYS` 預設關；打開前要先等 SP-881 的結論、必要時排除來源。
- 模型與每日上限是伺服器的，會員不能自己選模型（他付錢也一樣）。
- 「正在看哪一塊」是 DOM 猜的（最近的 `.card`／`section`／`.panel`…）；猜錯時頂端看得到、可以改成整頁。結構化的數字目前只有賽事計算機的結果與分段、課表課卡（SP-884）；其他卡送看得到的文字。
- 快取在記憶體，重啟就沒了；次數依伺服器日期。

## Change History

| Date | Source | Ticket | Summary |
|------|--------|--------|---------|
| 2026-10-29 | feature | SP-997 | 設定頁照 mock A：每個來源一張卡（序號、狀態、測試、換 key／編輯、清除、站主的 ↑↓）；會員可以填自己的 Gemini key（Q2-2，會員的順序是 Claude → Gemini，本機模型與順序仍只限站主）；問 AI 回答上方「本機模型連不上，改用 Claude 回答」；`session.ai.key` 看任何來源。測試：`backend/tests/test_ai_providers_sp997.py`、`backend/tests/e2e/test_askai_sources_sp997_e2e.py` |
| 2026-10-29 | feature | SP-997 | 後端：來源清單與順序（`backend/ai/providers.py`）——站主的本機模型（OpenAI 相容端點＋模型＋可選 key）、Anthropic key、Gemini key（REST `generateContent`，`TRC_GEMINI_API_KEY` 或加密存），預設本機優先，失敗換下一個；本機回答不算次數；`provider`／`tried`；費用線的 provider；每個來源的「測試」。測試：`backend/tests/test_ai_providers_sp997.py` |
| 2026-10-28 | maintenance | SP-985 | `spend.PRICE` 改照官方價目頁（2026-10-28）：Sonnet 5.5 由推估的 3／15 改成 2／10，補 Haiku 5.5（0.10／0.50）、Opus 5.5（4／20）。測試：`backend/tests/test_ai_spend_sp882.py` |
| 2026-10-28 | feature | SP-883 | 使用者要求開放：會員的「問 AI」預設開（沒 key 也看得到），`/api/v1/session` 的 `ai` 對會員多 `own: true`；沒 key 的會員對話框改成提醒＋連到設定頁 key 欄位（亮框），不能送出，存好 key 同一個對話框就能問。測試：`backend/tests/e2e/test_askai_own_key_sp883_e2e.py`、`test_ai_own_key_sp883.py` |
| 2026-10-28 | feature | SP-883 | 新增：會員用自己的 key（`TRC_AI_MEMBER_KEYS` 開著時，所有登入會員）：key 加密存在自己的 `ai.json`、次數記在自己的 `ai_usage.json`、絕不拿站主的環境變數 key；log 遮罩加上請求者自己的 key；`data_registry` 把 `ai.json` 列為 SECRET；呼叫鎖依人分開；`purpose=ask_own_key`；`POST /api/v1/ai/settings/test`（GET /v1/models，不計費，每天 10 次）；餘額不足 `AI_NO_CREDIT`；設定頁「測試」與會員版文字。測試：`backend/tests/test_ai_own_key_sp883.py` |
| 2026-10-20 | feature | SP-785 | 新增：`backend/ai`（config 金鑰與開關、context 彙總與清理、qa prompt／呼叫／解析／快取／每日上限、medical）、`/api/v1/ai/*`、`TRC_ANTHROPIC_API_KEY`（或 `_FILE`）或設定頁加密存放、log 遮罩、`/api/v1/session` 的 `ai`。測試：`backend/tests/test_ai_qa_sp785.py` |
| 2026-10-21 | maintenance | SP-885 | 英文介面巡一次：「正在問」對課表課卡寫課名與日期（`data-ai-title`，不再是卡片整串 aria-label）、對計算機結果／分段卡用頁面語言的標題（結果區本身的標題還沒翻）。測試：`backend/tests/e2e/test_askai_en_sp885_e2e.py` |
| 2026-10-21 | feature | SP-884 | 新增：`askai.js` 讀卡前發 `askai:describe`，賽事計算機（`#results`／`#profile-sec`）與課表課卡在那時填 `data-ai-facts`／`data-ai-rules`（整場數字＋分段、課的數字＋結構、提示與完成度判斷）；最後點過的卡被重畫時用 `data-uid`／`id` 找回。測試：`backend/tests/e2e/test_askai_facts_sp884_e2e.py` |
| 2026-10-21 | feature | SP-882 | 新增：`backend/ai/spend.py`，每次模型呼叫的 token 與 USD 寫成 `logs/ops/api_calls.lp`（共用 `api_call` schema），`deploy/ops-monitoring/telegraf.conf` 加 `inputs.tail` 與只收 `api_call` 的 `feeder` output。測試：`backend/tests/test_ai_spend_sp882.py` |
| 2026-10-20 | feature | SP-786 | 新增：`static/askai.js`（全站一顆問 AI、帶入正在看的卡、預設問題、App 規則／AI 推論標示、「還不懂？問 AI」）、`shell.js` 載入與 `?` 說明框掛點、設定頁 `#askai`＋`ai_settings.js`、`shell.json`／`settings.json` 中英文字。測試：`backend/tests/e2e/test_askai_sp786_e2e.py` |
