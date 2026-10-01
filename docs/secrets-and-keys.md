# 金鑰與加密資料

這個專案有兩類敏感資料，都用同一把 Fernet 金鑰加密：

| 資料 | 放在哪 | 說明 |
|---|---|---|
| TrainingPeaks client secret（WKO5 client） | repo 裡的 `backend/settings/tp_client.enc`（只有密文） | 解法見 `docs/wko5-internals/trainingpeaks-auth.md` |
| COROS / TrainingPeaks 登入 token、cookie | 本機資料庫 `~/.wko5coach/wko5coach.db` | 帳密本身預設不保存 |
| COROS / TrainingPeaks 密碼（只有勾「記住密碼」時） | 本機資料庫 `sync_state.coros_password_sealed`、`tp_password_sealed`（+ `tp_username`） | 見下方「記住密碼」 |

## 記住密碼（2026-10-01，使用者要求）

- 設定頁 COROS / TrainingPeaks 登入旁的「記住密碼」，**預設不勾**。
- 勾選後登入成功，密碼用同一把金鑰的 `secrets.seal()`（Fernet）加密存進 `sync_state`；
  資料庫裡沒有明文，log 不印，任何 API 都不回傳，狀態 API（`/auth/coros/status`、`/auth/tp/status`）只回 `password_saved: true/false`。
- **取消勾選**（`PUT /api/v1/auth/{coros|tp}/remember {"remember": false}`）或**登出**會立刻刪除。
- 用途只有自動重新登入：COROS token 過期（24 小時）或回「Access token is invalid」（result 1019），
  TP 的 refresh token／網站 cookie 失效時，自動登入**一次**、原本的請求重試**一次**；
  有鎖，同時多個請求只會登入一次（COROS 只認最後一次登入）；重新登入失敗就回報 `COROS_AUTH_REQUIRED`／`TP_AUTH_REQUIRED`，不會重試迴圈。
- 換金鑰後存的密碼也解不開：重新登入並勾選一次即可。

## 金鑰放在哪

- **執行時讀取順序**：環境變數 `WKO5COACH_SECRET_KEY` → 檔案 `~/.wko5coach/secret.key`。
- **來源**：金鑰**不在本 repo**，放在 private repo **dotfiles**：
  `dotfiles/keys/wko5coach/secret.key`，說明在 `dotfiles/keys/README.md`。
- **部署**：chezmoi 用 `dotfiles/private_dot_wko5coach/private_secret.key.tmpl` 把它部署到
  `~/.wko5coach/secret.key`。
- 密文（本 repo）和金鑰（dotfiles）分開放：兩個 repo 都外洩才會被解開。

## 換一台電腦開發

1. 在新電腦裝好 chezmoi 並指向 dotfiles，執行 `chezmoi apply`，`~/.wko5coach/secret.key` 就會出現。
   沒用 chezmoi 的話，把 `dotfiles/keys/wko5coach/secret.key` 複製過去，或設定 `WKO5COACH_SECRET_KEY`。
2. clone 本 repo，照常啟動。TP client secret 會從 `tp_client.enc` 解出來。
3. COROS / TP 需要重新登入一次（資料庫是本機的，不跟著 repo 走）。

## 金鑰遺失或要輪替

- TP client secret 可以用 `backend/scripts/decode_tp_client_secret.py` 從本機 WKO5.exe 重新解出，
  再用 `backend/scripts/seal_tp_client.py` 以新金鑰重建 `tp_client.enc`。
- 換金鑰後，舊的密文與資料庫裡的 token 都解不開：重建 `tp_client.enc`、重新登入 COROS / TP，
  再更新 dotfiles 裡的金鑰並 `chezmoi apply`。細節見 `dotfiles/keys/README.md`。
