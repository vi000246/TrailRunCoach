# 訂閱付款（SP-328）：Polar，跟 inReachLiveTrack 共用

程式在 `backend/billing/`（設計說明在 `__init__.py` 開頭）、`backend/api/billing.py`（路由）、`backend/static/plans.html`（方案頁）。測試 `backend/tests/test_billing_sp328.py`（假的 Polar，不連網路）。

## 1. 跟 inReachLiveTrack 共用什麼、各自做什麼

inReachLiveTrack 的實作：`docs/srs/livetrack-subscription-billing.srs.md`、`v2/server/src/lib/polar-*.ts`、`billing-*.ts`、`routes/billing.ts`。

| 項目 | inReachLiveTrack | TrailRunCoach | 共用？ |
|---|---|---|---|
| 金流商 | Polar（代收款項的賣方，merchant of record） | 同一家 | ✅ 同一個 Polar organization（商戶、撥款、稅由 Polar 處理） |
| 產品 | basic、pro（各一個 Polar product） | 月繳、年繳兩個 product（NT$99／月、NT$890／年） | 同一個 organization 裡的不同 product；兩邊**只認自己的 product id** |
| 結帳 | `POST /v1/checkouts/`，`external_customer_id = user.id`（UUID） | 同一支 API，`external_customer_id = trc:<user id>` | 呼叫方式相同；客戶編號加前綴，兩邊不會認錯人 |
| 付款回呼（webhook） | `POST /api/billing/webhook`，Standard Webhooks 驗簽、±5 分鐘、`webhook-id` 去重、只吃 `customer.state_changed`、亂序守衛 | `POST /api/v1/billing/webhook`，**同一套規則** | Polar 的 webhook 可以設多個網址，每個 app 各登記一個、各自的 secret；同一個事件兩邊都會收到，各自只處理自己的客戶與 product |
| 訂閱紀錄 | Cloudflare D1：`billing_subscriptions`、`billing_events`、`terms_consents` | NAS 的 `accounts.db`：`billing_subscriptions`、`billing_events` | ❌ 各自存（兩個 app 的資料庫不同，跨網路共寫一張表只會多一個故障點）。真正的狀態在 Polar，兩邊都能隨時問 Polar 對帳 |
| 權益推導 | basic／pro；續扣失敗寬限 7 天；期末取消到期才降；`canceled` 但還沒到期仍算 | free／subscriber（SP-330）；**同樣的規則**：寬限 7 天、期末取消到期才降、`canceled` 未到期仍算 | 規則一致（`backend/billing/entitlement.py` 照 `billing-entitlement.ts` 寫） |
| 到期降級 | 每日 cron（`expireDuePlans`）＋對帳 cron | 不用 cron：`plan_expires_at` 過了，`membership.role_of_user` 自動回到免費；續訂中的訂閱到期日＝期末＋3 天，漏收續訂事件最多晚 3 天降級，使用者按「重新整理」或打開方案頁就會再問 Polar | 做法不同（TrailRunCoach 的方案本來就有到期時間） |
| 自助管理 | Polar customer portal（換卡、取消、收據） | 同一個 portal | ✅ |
| 換方案（basic↔pro） | 有 | 不需要（只有一種訂閱；月繳↔年繳請在 portal 取消再訂） | — |
| 管理工具（價格快取、實收計算機、不一致清單、補償折扣） | 有（M3） | **沒做**（這張不含；要的話另開單） | — |
| 退款 | 不做自助退款，站長在 Polar 後台處理 | 同 | ✅ 站長在 Polar 後台退款並撤銷訂閱；webhook 送來「沒有有效訂閱」就降回免費 |
| 電子發票／收據 | Polar 寄收據 | 同 | ✅（是否還要另開統一發票，見 §4） |
| 刪除帳號 | 守衛：先在 Polar 取消才能停用 | 刪除前自動請 Polar 停止續訂（`cancel_at_period_end`）；Polar 連不上就不刪 | 做法不同 |
| 金鑰 | D1 settings 或 `wrangler secret` | 只從環境變數（或 `*_FILE`）讀，log 會遮掉 | 各自一把 token、各自一個 webhook secret（同一個 organization 可以發多把） |

**同一個人兩邊都有帳號**：Polar 在同一個 organization 裡以 email 辨識客戶。若某人已經是 inReachLiveTrack 的 Polar 客戶，再用同一個 email 在 TrailRunCoach 結帳，Polar 可能拒絕建第二個同 email 的客戶（要在沙盒驗證）。根本解法是 SP-329 的共用登入：兩邊用同一個帳號編號當 `external_customer_id`，一個客戶底下同時有兩邊的 product，兩邊各自只看自己的。這一點要使用者決定（見回報）。

## 2. 站主要做的事（👤）

1. 在 Polar（inReachLiveTrack 用的那個 organization，或另開一個）建兩個 product：
   - 月繳：NT$99，每月續訂
   - 年繳：NT$890，每年續訂
   先在 **sandbox**（`https://sandbox.polar.sh`）建一組做測試，正式環境再建一組。
2. 建一把 Organization Access Token（scopes：`checkouts:write`、`customers:read`、`customer_sessions:write`、`subscriptions:write`；名稱以 Polar 後台為準）。
3. 在 Polar 的 Webhooks 加一個端點：
   - URL：`https://coach.yichlin.com/api/v1/billing/webhook`
   - 格式：Raw；事件至少勾 `customer.state_changed`
   - 存下它的 secret
4. 在 NAS 的 env（repo 外的 `.env`，不 commit）設定：

   | 變數 | 值 |
   |---|---|
   | `TRC_POLAR_TOKEN` | 第 2 步的 token（密鑰） |
   | `TRC_POLAR_WEBHOOK_SECRET` | 第 3 步的 secret（密鑰） |
   | `TRC_POLAR_ENV` | `sandbox`（測試）或 `production` |
   | `TRC_POLAR_PRODUCT_MONTH` | 月繳 product id |
   | `TRC_POLAR_PRODUCT_YEAR` | 年繳 product id |

   兩把密鑰也可以用 `TRC_POLAR_TOKEN_FILE`／`TRC_POLAR_WEBHOOK_SECRET_FILE` 指向 Docker secret 檔。
5. 重啟容器。方案頁（`/plans`）會出現付款按鈕；sandbox 模式會標「測試模式」。用 Polar 沙盒的測試卡付一次，確認方案變成訂閱、取消後到期降級。

沒設定（或少任何一項）時，方案頁顯示「訂閱還沒開放」，程式不會呼叫 Polar。

⚠️ **正式站不要長時間開 sandbox**：sandbox 的測試卡不會扣錢，開著的期間任何人都能用測試卡拿到訂閱方案。測完就換成 production 的那組，或把五個變數拿掉。

## 3. 怎麼運作

- 方案頁 →〔前往付款〕→ `POST /api/v1/billing/checkout` → Polar 的付款頁 → 付完回 `/plans?billing=ok`，頁面每 5 秒問一次 Polar（最多 6 次）直到方案生效；取消付款回 `/plans?billing=cancel`。
- Polar 每次訂閱狀態改變（新訂、續扣、續扣失敗、取消、撤銷）就送 `customer.state_changed`；驗簽通過、沒處理過、比上一次新，才寫進帳號。
- 免費層被擋（SP-330 的升級提示）時，提示後面多一個「看方案與訂閱」連結到 `/plans`；「更多」選單也有「方案與訂閱」。

## 4. 還沒定的事

- **統一發票**：Polar 是 merchant of record（賣方是 Polar，它開收據、處理各國稅）；台灣消費者是否還需要站方另開電子發票、站方對 Polar 的撥款怎麼報稅，要問會計師（月營收未達 NT$50,000 免辦稅籍，SP-321）。
- **試用期**：目前沒有。要的話在 Polar 的 product 設 trial，程式已把 `trialing` 當訂閱中。
- **退款規則**、七日解除權：寫進服務條款（SP-60），條款裡的【待定：SP-328】要補上金流商名稱「Polar」與規則。
