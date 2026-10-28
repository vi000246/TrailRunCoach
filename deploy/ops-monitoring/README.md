# 營運監控：容器 CPU／記憶體、會員數與本月訂閱收入（SP-388、SP-803）

NAS 上既有的 Grafana＋InfluxDB 加一組 TrailRunCoach 的監控：

| 看什麼 | 從哪來 | 告警（Grafana 既有的通知管道） |
|---|---|---|
| `trailruncoach` 容器的 CPU（核） | Telegraf `inputs.docker` → `docker_container_cpu.usage_percent`（100 ＝ 一核） | 最近 7 個完整的日子（台灣時間）**每天**的尖峰都 > 2 核 |
| 記憶體（GiB，不含可回收快取，同 `docker stats`） | `docker_container_mem.usage_percent × limit` | 同上，每天的尖峰都 > 2.5 GiB |
| 會員數／封頂 | App 每小時寫的 `<資料夾>/logs/ops/metrics.json`（`backend/ops_daily.py`），Telegraf `inputs.file` → `trc_members` | 封頂的 80 % 以上 |
| 本月訂閱收入（SP-803） | App 每小時寫的 `<資料夾>/logs/ops/revenue.json`（`backend/billing/revenue.py`：從 app 自己的訂閱紀錄用牌價估，不打 Polar），Telegraf `inputs.file` → `trc_revenue` | 照現在的訂閱、月底會到免辦稅籍門檻 NT$50,000 的 80 % 以上 → 去問會計師要不要登記營業（SP-321） |
| AI 問答的 token 與 USD（SP-882） | App 每呼叫一次模型就在 `<資料夾>/logs/ops/api_calls.lp` 加一行（`backend/ai/spend.py`），Telegraf `inputs.tail` → bucket **`feeder`** 的 `api_call`，出現在共用的「💸 API 使用量」dashboard（`project = trailruncoach`） | 不另設（看共用 dashboard） |

「尖峰」＝當天 5 分鐘平均的最大值（一兩秒的突波不算）。資料還不滿 7 天時，告警看的值是 0，不會叫。
升級的意思見 SP-321 起始方案第 6 條（`docs/research/membership-cost-tiers.md` §7）：搬 Hetzner CX33，約 NT$345／月。

收入的數字是估計：app 不記每筆實收金額，用各訂閱的月繳／年繳牌價（`backend/billing/__init__.py` 的 `PRICES`）與
本期起始日（`current_period_end` 往前一個月／一年）算「本月已收」，再加上本月內還會續訂的（「月底估計」）；告警看月底估計，
所以會在跨線**之前**叫。只算這個 app 的訂閱——同一個 Polar 帳號的 inReachLiveTrack 收入不在裡面。讀不到 `accounts.db` 時
`revenue.json` 只有 `ok: 0`、沒有金額（不寫成 0），告警那段時間沒有資料。

每日報表（每人 CPU 秒、免費層超標、回測排隊、COROS／Garmin 被擋、會員數、本月訂閱收入）**不經過這裡**：app 自己每天 03:30
寫 `<資料夾>/logs/ops/YYYY-MM-DD.md`、留 31 天，管理 › 營運報表看得到，不推播。

## 檔案

| 檔 | 用途 |
|---|---|
| `telegraf.conf` | docker 輸入（只收 `trailruncoach`）＋ `metrics.json` 檔案輸入 → InfluxDB v2；`api_calls.lp` tail 輸入 → `feeder` bucket（第二個 output，只收 `api_call`） |
| `docker-compose.yml` | NAS 上沒有 Telegraf 時才用：一個只做這件事的 `telegraf-trc` 容器 |
| `grafana-dashboard.json` | 面板「TrailRunCoach 營運」（uid `trailruncoach-ops`），用 import 匯入 |
| `grafana-alert-rules.json` | 一個規則群組 `trailruncoach-ops`（每小時評估），四條告警 |

設定裡的值都是 placeholder，套用前換掉：

| placeholder | 是什麼 | 在哪查 |
|---|---|---|
| `<TRC_DATA_DIR>` | App 資料夾在 NAS host 上的路徑（容器裡的 `~/.wko5coach`） | `docker inspect trailruncoach` 的 Mounts，或 `/volume1/docker/trailruncoach/docker-compose.yml` 的 volumes |
| `<INFLUX_BUCKET>` | 共用 InfluxDB 的 bucket | homelab skill（共用 bucket） |
| `<INFLUX_DS_UID>` | Grafana 裡那個 InfluxDB **Flux** 資料來源的 uid | `GET /api/datasources` |
| `<FOLDER_UID>` | 告警規則放的 Grafana 資料夾 | `GET /api/folders`（沒有就建一個「TrailRunCoach」） |
| `<CONTACT_POINT>` | 通知管道的名稱——**跟「💸 API 使用量」dashboard 的告警同一個** | `GET /api/v1/provisioning/contact-points`、`GET /api/v1/provisioning/policies` |

`.env`（Telegraf 用，**不進 repo**）：`INFLUX_URL`（InfluxDB 的 LAN 位址，用 host IP 不用容器名）、`INFLUX_TOKEN`、`INFLUX_ORG`、`INFLUX_BUCKET`。

## 套用（在 NAS 上，主 session 做）

0. **先查現況**
   - 有沒有已經在收容器指標：`docker ps --format '{{.Names}} {{.Image}}' | grep -Ei 'telegraf|cadvisor'`；
     InfluxDB 裡有沒有 `docker_container_cpu`（Grafana Explore 或 `influx query`）。
   - App 的資料夾在 host 的哪裡（→ `<TRC_DATA_DIR>`），`logs/ops/` 等 app 部署這版之後才會出現。
   - Grafana 的 InfluxDB 資料來源是 Flux 還是 InfluxQL。這裡的查詢都是 **Flux**；只有 InfluxQL 的話，
     新增一個 Flux 資料來源（同一個 InfluxDB、同一個 org，token 要能讀 bucket）。
   - 「💸 API 使用量」的告警用的是哪個 contact point（→ `<CONTACT_POINT>`）。
1. **Telegraf**
   - 已經有 Telegraf 而且掛著 `/var/run/docker.sock`：把 `telegraf.conf` 的 `[[inputs.docker]]` 和 `[[inputs.file]]`
     兩段加進它的設定（`[[inputs.file]]` 的路徑要對到它掛進去的 `logs/ops`），重啟它。
   - 沒有：`/volume1/docker/telegraf-trc/` 放 `telegraf.conf`、`docker-compose.yml`（換掉 `<TRC_DATA_DIR>`）、`.env`，
     `cd /volume1/docker/telegraf-trc && PATH=/usr/local/bin:$PATH docker-compose up -d`。
   - 坑（2026-10-20 實際套用時撞到）：Synology 的 docker 不會替 bind mount 建資料夾——`<TRC_DATA_DIR>/logs/ops` 要先存在
     （`data/logs` 是 root 的，用 `docker run --rm -v <TRC_DATA_DIR>:/data --entrypoint sh trailruncoach:latest -c "mkdir -p /data/logs/ops"`）；
     telegraf 映像會降成 telegraf 使用者，`user: root` 不夠，要 `group_add` socket 的群組（`stat -c %g /var/run/docker.sock`，這台是 65538）。
2. **InfluxDB token**：照 grafana-api-usage skill 的「每專案 write-only token」做法，建一把只能寫那個 bucket 的 token
   （description `telegraf-trc write-only`），放進 `.env` 的 `INFLUX_TOKEN`。
   AI 問答的費用（SP-882）寫進 **`feeder`**：`<INFLUX_BUCKET>` 不是 `feeder` 時，這把 token 的 permissions 要再加一條
   `feeder` 的 write（沒有的話 `docker logs telegraf-trc` 會看到寫 `feeder` 的 401／403，其他兩組照常）。
   已經套用過的（2026-10-20 那次）：換上新的 `telegraf.conf`、重啟 `telegraf-trc` 就好，掛載不用動（`api_calls.lp` 也在 `logs/ops/`）。
   收入（SP-803）也一樣：新的 `telegraf.conf` 多一段讀 `revenue.json` 的 `[[inputs.file]]`，寫進原本的 `<INFLUX_BUCKET>`，token 不用改；
   面板重新 import（多兩格）、告警照第 4 步再 `POST` 新的那一條 `trc-revenue-near-tax-line`，再 `PUT` 一次群組。
3. **面板**：Grafana › Dashboards › New › Import，貼 `grafana-dashboard.json`，資料來源選那個 Flux 來源、bucket 填 bucket 名稱。
   或用 API：`POST /api/dashboards/import`，`inputs` 帶 `DS_INFLUXDB`（資料來源 uid）與 `VAR_BUCKET`。
4. **告警**：把 `grafana-alert-rules.json` 的 `<INFLUX_BUCKET>`、`<INFLUX_DS_UID>`、`<FOLDER_UID>` 換掉。
   **第一次**要逐條 `POST /api/v1/provisioning/alert-rules`（body 是 `rules[]` 的每一條，header 加
   `X-Disable-Provenance: true` 才能之後在 UI 改）——直接 `PUT` 整個群組時，Grafana 11.5 把帶 uid 的規則當成「更新」，
   規則還不存在就回 500。建完再 `PUT /api/v1/provisioning/folder/<FOLDER_UID>/rule-groups/trailruncoach-ops`（body 就是這個檔）
   設群組的評估間隔；之後改規則都用這個 PUT。
   規則沒有寫死通知管道（`notification_settings`），走 Grafana 的通知政策：有真的通知管道後，在通知政策加一條
   `project = trailruncoach` → 那個 contact point。2026-10-20 這台只有預設的 `example@email.com`（SMTP 關著），通知送不出去。

## 確認

- `docker logs telegraf-trc` 沒有錯誤（token 錯會一分鐘噴一次 401）。
- Grafana Explore（Flux）跑：
  ```
  from(bucket: "<INFLUX_BUCKET>") |> range(start: -15m)
    |> filter(fn: (r) => r._measurement == "docker_container_cpu" or r._measurement == "docker_container_mem" or r._measurement == "trc_members")
    |> filter(fn: (r) => r.project == "trailruncoach") |> last()
  ```
  三個 measurement 都有一筆。`trc_members` 要等 app 部署這版、開起來一小時內寫出 `metrics.json`（單人模式不寫）。
- 面板「TrailRunCoach 營運」：CPU、記憶體的線在動；「最近 7 天」兩格頭 7 天是 0；會員數／封頂跟管理頁的「營運報表」分頁一樣。
- 告警：Alerting › Alert rules 看到四條、狀態 Normal；要試通知管道，暫時把會員那條的門檻改成 0.001，
  一小時內（或按規則的「Test」）應收到通知，再改回 0.799。收入那條（`trc-revenue-near-tax-line`）同樣做法；
  還沒開始收款時 `share` 是 0，Normal。
- 收入（SP-803）：app 部署這版、開起來一小時內 `<TRC_DATA_DIR>/logs/ops/revenue.json` 出現（單人模式不寫）；Flux 查
  `trc_revenue` 的 `ok` 是 1、`projected` 跟管理 › 營運報表 › 本月訂閱收入的「月底」一樣。
- 每日報表：隔天 03:30 之後，管理 › 營運報表出現前一天的日期；NAS 上 `<TRC_DATA_DIR>/logs/ops/` 有那一天的 `.md`。
- AI 問答的費用（SP-882）：站主問一題（不是快取的答案）後，`<TRC_DATA_DIR>/logs/ops/api_calls.lp` 多一行；一分鐘內 Flux 跑
  ```
  from(bucket: "feeder") |> range(start: -1h)
    |> filter(fn: (r) => r._measurement == "api_call" and r.project == "trailruncoach") |> last()
  ```
  看得到 `input_tokens`、`output_tokens`、`cost_usd`；「💸 API 使用量」的「花費 by 專案」多一塊 `trailruncoach`。

## 會員數與 AI 費用為什麼走檔案

App 不拿 InfluxDB 的 token、也不對外寫東西：它只把會員數寫成自己資料夾裡的 `metrics.json`、把每次 AI 呼叫的
token 與 USD 加一行到 `api_calls.lp`（只有模型、token 數、毫秒、錯誤碼，沒有 key、問題、回答），Telegraf 唯讀掛載去讀。
token 只在 Telegraf 的 `.env`。Telegraf 沒在跑時 app 照常回答，那段時間的費用不會補進 Grafana（tail 只讀新的行）。
