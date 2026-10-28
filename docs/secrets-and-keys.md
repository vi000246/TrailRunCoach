# 金鑰與加密資料

本機會存的敏感資料都用同一把 Fernet 金鑰（`backend/settings/secrets.py`）加密：

| 資料 | 放在哪 | 說明 |
|---|---|---|
| COROS / TrainingPeaks 登入 token、cookie | 本機資料庫 `~/.trailruncoach/wko5coach.db` | 帳密本身預設不保存 |
| COROS / TrainingPeaks 密碼（只有勾「記住密碼」時） | 同一個資料庫的 `sync_state.coros_password_sealed`、`tp_password_sealed`（+ `tp_username`） | 見下方「記住密碼」 |
| AI 問答的 API key（在設定頁貼上時，SP-785） | 擁有者資料夾的 `ai.json`（`api_key`，權限 600） | 環境變數 `TRC_ANTHROPIC_API_KEY`（或 `_FILE`）有設時用它、不存檔；畫面只顯示最後 4 碼，log 不印。見 `docs/spec/ai-qa.spec.md` |
| 會員自己的 AI 問答 API key（SP-883，`TRC_AI_MEMBER_KEYS` 開著時） | 該會員資料夾 `users/<id>/ai.json`（`api_key`，權限 600） | 只用他自己的、拿不到擁有者的環境變數 key；畫面只顯示最後 4 碼，log 遮罩含請求者自己的 key；不進「下載我的資料」與備份（`data_registry` SECRET）；刪帳號跟資料夾一起刪 |

另外兩樣不加密、但也不會出現在 repo 或 API 回應裡：

| 資料 | 放在哪 | 說明 |
|---|---|---|
| Debug API 的 token（個人 API token，SP-371／SP-373） | 資料庫 `debug_tokens` 只存 SHA-256 雜湊；登入模式下 accounts.db `api_tokens` 也只存雜湊（SP-406） | 擁有者在設定頁產生（SP-373 起不用 PIN），只在產生時顯示一次，資料庫外流也還原不出來。見 `docs/debug-api.md` |
| 本機從正式站同步用的 token（SP-373） | 只在**本機**的環境變數 `TRC_UPSTREAM_TOKEN`（＋代理的 `TRC_PROXY_PASSWORD`） | 正式站產生的 debug token（`export:config`＋`export:plan`）；不進資料庫、不顯示在畫面、log 裡遮掉 |

repo 裡**沒有**任何金鑰、client secret、PIN 或密文。

## TrainingPeaks OAuth client（選用）

沒有 client 也能用：TP 同步會改走網站登入。要用 OAuth 時，自己申請一組 client，
用下面任一種方式提供（讀取順序由上到下）：

1. 環境變數 `TP_CLIENT_ID` + `TP_CLIENT_SECRET`
2. `~/.trailruncoach/tp_client.json`：`{"client_id": "...", "client_secret": "..."}`（放在家目錄，不在 repo 內）

設定頁「資料同步」會顯示目前用的是哪個來源（只顯示來源，不顯示值）。

## 記住密碼

- 設定頁 COROS / TrainingPeaks 登入旁的「記住密碼」，**預設不勾**。
- 勾選後登入成功，密碼用 `secrets.seal()` 加密存進 `sync_state`；資料庫裡沒有明文，log 不印，
  任何 API 都不回傳，狀態 API（`/auth/coros/status`、`/auth/tp/status`）只回 `password_saved: true/false`。
- **取消勾選**（`PUT /api/v1/auth/{coros|tp}/remember {"remember": false}`）或**登出**會立刻刪除。
- 用途只有自動重新登入：token 過期或失效時，自動登入**一次**、原本的請求重試**一次**；
  有鎖，同時多個請求只會登入一次；失敗就回報 `COROS_AUTH_REQUIRED`／`TP_AUTH_REQUIRED`，不會重試迴圈。

## 金鑰放在哪

- **讀取順序**：環境變數 `TRC_SECRET_KEY` → 檔案 `~/.trailruncoach/secret.key`。
- 兩者都沒有、而且還沒有任何密文時，第一次啟動會自動產生 `~/.trailruncoach/secret.key`（權限 600）。
- 已經有密文卻找不到金鑰時，app 會拒絕產生新金鑰（`SECRET_KEY_MISSING`），避免舊資料永遠解不開。
- 伺服器部署建議用環境變數或 secrets manager 提供金鑰。產生一把：

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

- 金鑰請自行備份（密碼管理器等），不要放進任何 git repo。`*.key` 與 `.env` 已在 `.gitignore`。

## 換一台電腦

1. 把 `~/.trailruncoach/secret.key` 複製到新電腦，或設定 `TRC_SECRET_KEY`。
2. 資料庫是本機的，不跟著 repo 走；沒搬資料庫的話，COROS / TP 重新登入一次即可。

## 金鑰遺失或要輪替

- 換金鑰後，舊的 token 與記住的密碼都解不開：重新登入 COROS / TP（要記住密碼就再勾一次）。
- 不影響訓練資料本身，只有登入狀態要重來。
