# Deploy Guide: WKO5 Coach — Docker

## Metadata
- **Service**: wko5coach
- **Mode**: Docker Compose (本機)
- **Target**: localhost:8000
- **Last Updated**: 2026-05-15

---

## 前置條件

| 條件 | 確認方式 |
|------|---------|
| Docker Desktop 已安裝並執行 | `docker info` |
| `~/WKO5/` 目錄存在（WKO5 資料） | `ls ~/WKO5/` |
| Node.js ≥ 20（build 時用） | 在 Docker multistage build 中自動處理 |
| Python 3.12（build 時用） | 在 Docker image 中自動 |

## Volume 說明

| Host | Container | 用途 |
|------|-----------|------|
| `~/.wko5coach/` | `~/.wko5coach/` | SQLite DB (`wko5coach.db`) |
| `~/WKO5/` | `~/WKO5/` | WKO5 訓練資料（.wko4 / .fit）|

注意：路徑以 `$HOME` 展開，確保 container 內的 `Path.home()` 與 host 一致。

---

## 部署步驟

### Step 1 — 建構並啟動容器

```bash
cd "<repo>reverse"
./deploy.sh
```

等待輸出：
```
✓ WKO5 Coach  →  http://localhost:8000
✓ Portainer   →  http://localhost:9000
```

### Step 2 — Bootstrap athlete（第一次執行）

```bash
curl -s -X POST http://localhost:8000/api/v1/athletes/bootstrap | python3 -m json.tool
```

預期：`{"created": ["Athlete"]}`

### Step 3 — 掃描 WKO5 目錄

```bash
curl -s -X POST http://localhost:8000/api/v1/scan | python3 -m json.tool
```

預期：`{"new": 1012, "skipped": 0, "errors": 0, "total": 1012}`（數字依實際檔案數）

### Step 4 — 驗證 API

```bash
curl -s "http://localhost:8000/api/v1/workouts?per_page=3" | python3 -m json.tool
```

預期：`{"total": 1012, ...}` 或類似

### Step 5 — TrainingPeaks 登入（可選）

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/tp/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "YOUR_TP_EMAIL", "password": "YOUR_TP_PASSWORD", "athlete_id": 1}' \
  | python3 -m json.tool
```

預期：
```json
{
  "authenticated": true,
  "tp_athlete_id": 123456,
  "user_type": "Athlete",
  "premium": true,
  "can_download": true
}
```

> 注意：`can_download: false` 表示帳號非 premium/coach，TP 下載功能不可用。

### Step 6 — 觸發 TP Sync（需登入完成）

```bash
curl -s -N "http://localhost:8000/api/v1/sync/start?since=2026-01-01" \
  -H "Accept: text/event-stream"
```

SSE 串流輸出每個 workout 的下載進度，最後一行為：
```
data: {"status": "complete", "total_downloaded": N, "total_checked": M}
```

### Step 7 — 設定 FTP（選填）

```bash
curl -s -X PUT http://localhost:8000/api/v1/athletes/1/settings \
  -H 'Content-Type: application/json' \
  -d '{"ftp_w": 250, "effective_date": "2026-05-15"}' \
  | python3 -m json.tool
```

---

## 監控 & 日誌

```bash
# 即時 log
docker compose logs -f wko5coach

# 容器狀態
docker compose ps

# 重啟
docker compose restart wko5coach

# 停止全部
docker compose down
```

---

## 更新部署

```bash
cd "<repo>reverse"
git pull           # 若有版本控制
./deploy.sh        # rebuild + restart
```

Docker Compose 會重新 build image（`--build`）並熱替換容器。DB 資料在 volume 中持久化，不受影響。

---

## 常見問題

| 症狀 | 原因 | 解法 |
|------|------|------|
| `TP_AUTH_REQUIRED` | 未登入或 token 過期 | 重新 POST `/api/v1/auth/tp/login` |
| `can_download: false` | 帳號非 premium/coach | 升級 TP 帳號或使用手動 .fit 匯入 |
| PMC 圖表空白 | 尚無 FIT 功率資料 | 完成 TP Sync 後刷新 |
| Bootstrap 找不到 athlete | `~/WKO5/` 目錄結構不符 | 確認 `~/WKO5/Athlete/` 存在 |
| DB locked | 多個 uvicorn worker | Dockerfile CMD 已固定單 worker，若手動啟動加 `--workers 1` |

---

## TP Sync 技術備忘（逆向工程確認，2026-05-15）

WKO5 使用 ROPC OAuth，password grant **不含** client_id：
```
grant_type=password&username={}&password={}&scope=fitness+baseactivity+users+metrics+software+groundcontrol
```
Refresh：`grant_type=refresh_token&refresh_token={}&client_id=WKO5&client_secret=`

FIT 下載：`fitness/v6/.../filedata/{fileName}` → `{"data": base64(gzip(fit))}` → 解壓得原始 FIT。

下載需要 **premium 或 coach 帳號**（binary: `"Download is allowed only from premium and coach accounts."`）。
