# 多使用者模式：帳號資料庫與擁有者遷移（SP-384）

> 狀態：資料面已完成（SP-384）。**登入（SP-58）還沒做之前，不要在正式環境打開 `WKO5COACH_ACCOUNTS`**：
> 打開之後，排程會開始掃 `accounts.db` 裡的使用者，但沒有任何方式能以使用者身分登入。
> 規格：`docs/plans/auth-and-demo.plan.md` §2.2、§4.3、§5。

## 1. 放在哪裡

| 東西 | 位置 | 說明 |
|---|---|---|
| `accounts.db` | `<server-root>/accounts.db` | users、identities、sessions（只存 token 的 sha256）、shares、waitlist |
| 使用者的資料 | `<server-root>/users/<id>/` | 一個人一個資料夾，結構和 `~/.wko5coach` 一樣（自己的 `wko5coach.db`、`plan.json`、`fit/`、快取） |
| 擁有者的資料 | `~/.wko5coach`（不動） | `users` 第 1 列，`role=owner`，`tenant_dir` 原地指向這裡 |

`<server-root>` 是 `WKO5COACH_SERVER_ROOT`，沒設就是 `WKO5COACH_HOME`（`~/.wko5coach`）。
`accounts.db` 和 `users/` 在資料登錄表（`backend/data_registry.py`）標成伺服器自己的檔案，**不會進擁有者的備份**——那是別人的資料。
整台伺服器的備份（含 `accounts.db` 與所有 `users/`）在 SP-387 第 7 條。

## 2. 擁有者遷移（原地，零搬移）

1. 停掉 app（`docker compose stop`），把 `~/.wko5coach` 整個複製一份當備份。
2. **把 server key 搬進環境變數**：多使用者模式只吃 `WKO5COACH_SECRET_KEY`，不讀任何租戶資料夾裡的 key 檔
   （擁有者的 `~/.wko5coach` 也是一個租戶資料夾）。
   ```bash
   cat ~/.wko5coach/secret.key      # 把這串貼進 NAS 的 .env：WKO5COACH_SECRET_KEY=<那串>
   ```
   沒設就打開多使用者模式，app 啟動時會停下來（`ServerKeyRequired`），不會另外產生一把新的 key。
   同一把 key 繼續解得開既有的 COROS／TP 憑證；確認 app 正常之後，舊的 `secret.key` 檔可以移走。
3. 在 `.env` 加 `WKO5COACH_ACCOUNTS=1`，啟動。啟動時會建 `accounts.db`，`users` 第 1 列是擁有者、指向現有資料夾（重跑不會重建）。
4. 驗證：擁有者登入後，總覽、PMC、課表 API 的回應要和遷移前完全相同
   （自動測試：`backend/tests/test_accounts_sp384.py::test_the_owner_answers_the_same_signed_in_as_before`）。

擁有者登入後仍然是原本的租戶（id `owner`、同一個資料夾、WKO5 資料夾、所有權限），所以快取、課表、同步狀態都不會變。

## 3. 只屬於擁有者的東西

一般使用者的租戶讀不到（`tenancy.OWNER_ONLY_CAPS`；`backend/tests/test_accounts_sp384.py`）：

- WKO5 資料夾（各模組的 `ATHLETE_DIR` 一律經過 `settings/paths.for_tenant`）
- 中央氣象署授權碼（`CWA_API_KEY` 或 `weather.json`）：使用者的天氣改用 Open-Meteo
- TrainingPeaks 的 WKO5 client（`TP_CLIENT_ID`／`tp_client.json`）：TP 條款不准把 developer credentials 給別人
- 整個資料夾的備份與還原（使用者要的是「下載我的資料」，SP-386）
- 擁有者的 AI 金鑰（SP-306 做了之後）

## 4. 每日自動同步

排程每分鐘掃一次所有租戶（擁有者＋每個沒停用的使用者），在各自的資料庫裡讀各自的
`sync.schedule.daily_time`。使用者的時間會固定往後錯開 0～29 分鐘（依 id 算，每天一樣），
擁有者不錯開。每週 60 天檢查也逐租戶跑；補雨量與每日備份只跑擁有者的。

## 5. 還沒做的（別的單）

- Google 登入、註冊畫面、封頂與候補、登入攔截：SP-58
- 公開路徑白名單、拿掉密碼 proxy、固定網域：SP-387
- 刪除帳號、下載我的資料：SP-386
- 方案與權限 scope（`user`／`subscriber`／`owner`）：SP-330
- 管理後台：SP-351
