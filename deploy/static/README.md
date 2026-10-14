# 靜態示範（GitHub Pages）

公開示範的預設做法：把示範模式匯出成一個**純靜態網站**（HTML + JSON），放在 GitHub Pages，
不需要任何伺服器：<https://vi000246.github.io/TrailRunCoach/>
（repo 轉 private 前搬到 Cloudflare Pages `demo.yichlin.com`，擁有者照 §2b 一步一步做；搬好之前這裡的網址先不改。）

- 一位**虛構跑者**一年的資料（`python -m backend.demo.build` 產生，跟任何真人無關）。
- 能看：訓練總覽、課表、圖表分析（趨勢 + 部分活動的單次活動圖表）、週期規劃、活動列表與成就、路段、賽事計算機。
- 能改：**課表**（拖曳／編輯／新增／刪除課、設休息日、排入建議的測試）。修改只存在訪客自己的瀏覽器
  （localStorage），banner 的「重設示範」會清掉。
- 不能改：其他所有寫入（週期規劃的賽事和階段、課表偏好、依實際進度重排、推送到手錶、上傳 GPX、活動編輯…）
  會顯示「唯讀示範：這個操作在示範版不能用」。
- 賽事計算機：預設輸入、快速選項（10K／半馬／全馬／越野 30K／玉山 2 日）、三種努力目標和每一場賽季賽事的結果
  在匯出時**預先算好**；改了其他輸入會顯示「示範版只預先算好預設輸入的結果」。

## 1. 匯出

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium               # 匯出用的無頭瀏覽器（只要裝一次；升級 playwright 後要再裝）
python -m backend.demo.build --root ~/trc-demo      # 示範資料（已經有就跳過）
python -m backend.demo.export_static --root ~/trc-demo
#   → dist/static-demo/   （.gitignore 已排除 dist/）
```

選項：

| 選項 | 說明 |
| --- | --- |
| `--out DIR` | 輸出資料夾（預設 `dist/static-demo`；只會覆蓋之前匯出的資料夾） |
| `--activities N` | 有「單次活動」圖表的活動數（預設 40，最新的 + 比賽／測試／登山 + 最長／最累的；`0` = 全部，輸出會大很多） |
| `--no-browser` | 跳過無頭瀏覽器那一輪（只跑程式產生的請求；少了頁面上點出來的部分，只用來快速試） |

匯出會先把 `--root` **複製到暫存資料夾**再跑（app 會在資料旁邊寫快取），原本的資料不會被改；它拒絕
`~/.wko5coach` 和 `~/WKO5`，也不連任何外部服務（瀏覽器裡除了 cdnjs 的圖表／地圖函式庫，其他網路請求一律擋掉）。
跑完會印出摘要（頁數、JSON 筆數、大小），並檢查輸出裡沒有本機路徑或使用者名稱（有的話結束碼 2，不要發佈）。

### 它怎麼運作

1. 在同一個 Python 行程裡用示範模式啟動 app（FastAPI TestClient，不開 port）。
2. 無頭 Chromium 打開每一頁，點過只改變畫面的控制項（分頁、週／月／年、上一期、圖表目錄、單次活動、
   路段篩選、賽事計算機的快速選項和賽事…）；瀏覽器的每個請求都由 Playwright 攔下、交給 app 回答，
   所以頁面送出的 URL 就是靜態站會送的 URL。
3. 程式再補上瀏覽器沒點到的參數：圖表的每個日期預設（7 天／42 天／90 天／1 年／今年／全部）、運動類型篩選、
   每張圖的切換（日週月季年、近 N 天、配速／功率、顯示方式）、選中活動的單次活動圖表、每一筆活動的細節、
   課表前後幾個月／幾週的日曆、每一個路段。
4. 寫出：頁面放在根目錄（`index.html` = 示範首頁，`overview.html`、`schedule.html`、`charts.html` …），
   `static/` = 頁面的 JS／CSS，`data/<hash>.json` = 每個 GET 的回應（`hash` = FNV-1a 64 of「解碼後的路徑 +
   依名稱排序的查詢參數」），`data/p<hash>.json` = 預先算好的計算結果，`.nojekyll`、`export.json`（摘要）。
5. 每一頁最前面注入 `static/trc_static.js`（原始碼 `backend/demo/static_shim.js`）：把 `/api/v1/...`
   對到 `data/` 的檔案、把頁面連結改成靜態檔、時鐘固定從匯出那天開始（所以「今天」跟資料一致）、
   課表修改存在 localStorage、其他寫入一律拒絕。沒有存到的資料顯示「示範版沒有這筆資料」，不會整頁壞掉。

全部是相對網址，放在任何子路徑都能用（`https://<user>.github.io/<repo>/`、本機 `http://localhost:8765/TrailRunCoach/`）。

本機先看一下：

```bash
mkdir -p /tmp/site && cp -r dist/static-demo /tmp/site/TrailRunCoach
python -m http.server 8765 --directory /tmp/site     # 開 http://localhost:8765/TrailRunCoach/
```

## 2. 發佈到 GitHub Pages（`gh-pages` 分支）

建議做法：**手動把 `dist/static-demo` 推到一個只放網站的 `gh-pages` 分支**。

為什麼不用 GitHub Actions 在雲端建置：匯出需要示範資料（約 100 MB 的 FIT 和快取，不在 repo 裡）
和無頭瀏覽器，跑一次要幾分鐘；把資料或建置結果 commit 進 `main` 會讓主分支的歷史一直變大。
`gh-pages` 是一個跟 `main` 沒有共同歷史的孤立分支，每次發佈只保留一個 commit（強制推送覆蓋），
所以 repo 不會因為示範資料越來越大。

第一次（建立孤立的 `gh-pages` 分支，用另一個 worktree，不影響你目前的工作目錄）：

```bash
git worktree add --detach ../trc-gh-pages
cd ../trc-gh-pages
git checkout --orphan gh-pages
git rm -rf . >/dev/null 2>&1; git clean -fdx
cp -r ../TrailRunCoach/dist/static-demo/. .
git add -A
git commit -m "static demo $(date +%F)"
git push -u origin gh-pages
```

GitHub 上：**Settings → Pages → Build and deployment → Source: Deploy from a branch → `gh-pages` / `(root)`**。
幾分鐘後網站在 <https://vi000246.github.io/TrailRunCoach/>。

之後每次更新（重新匯出後）：

```bash
cd ../trc-gh-pages
git rm -rf . >/dev/null 2>&1; git clean -fdx
cp -r ../TrailRunCoach/dist/static-demo/. .
git add -A
git commit --amend -m "static demo $(date +%F)"      # 只留一個 commit
git push --force origin gh-pages                      # 只對 gh-pages 強制推送
```

注意：

- `.nojekyll` 一定要在根目錄（匯出已經加了）：沒有它 GitHub 會用 Jekyll 處理檔案。
- GitHub Pages 的限制：網站 ≤ 1 GB、單檔 < 100 MB、每月流量軟上限 100 GB。預設匯出大約幾十 MB。
- 示範的「今天」是匯出那天；資料想保持新鮮，就定期重新 `build` + `export_static` 再推一次。
- repo 改成 private 的話，免費方案的 GitHub Pages 會停用：先照 §2b 搬到 Cloudflare Pages。

（替代做法：在 GitHub Actions 用 `actions/upload-pages-artifact` + `actions/deploy-pages` 部署一個預先建好、
放在 Release 附件裡的 zip。需要多維護一個 workflow 和一個 Release，目前不需要。）

## 2b. 搬到 Cloudflare Pages（repo 轉 private 之前要先做，SP-350）

個人帳號的 private repo 不能免費用 GitHub Pages，所以 **先把示範搬到 Cloudflare Pages、確認新網址打得開，再把 repo 轉 private**。
匯出和第 1 節完全一樣；只是發佈的地方換了。網域和固定 tunnel 的規劃跟商業版一起（`deploy/commercial.md` §1：`demo.yichlin.com`）。

👤 擁有者照順序做（要 Cloudflare 帳號，Claude 拿不到也不該拿到 token）。**第 7 步以前任何一步不通，就停在那裡，不要轉 private。**

0. 匯出（第 1 節）。在 repo 根目錄跑，輸出在 `dist/static-demo/`；跑完看最後印的摘要 `leaks` 是 `[]`（結束碼 0）。
   第一次在新電腦約 4 分鐘建資料 + 10 分鐘匯出。
1. 登入 Cloudflare、建專案（第一次）：
   ```bash
   npx wrangler login                       # 開瀏覽器登入 Cloudflare（token 存在本機 wrangler 設定，不進 repo）
   npx wrangler pages project create trc-demo --production-branch main
   ```
   （常用的話 `npm i -g wrangler`，之後指令去掉 `npx`。）
2. 發佈（之後每次重新匯出也只跑這行）：
   ```bash
   npx wrangler pages deploy dist/static-demo --project-name trc-demo --branch main
   ```
   wrangler 印出 `https://<hash>.trc-demo.pages.dev`；正式網址是 `https://trc-demo.pages.dev/`。
   先打開 `https://trc-demo.pages.dev/` 看一次總覽，再往下。
3. 加自訂網域：Cloudflare dashboard → Workers & Pages → `trc-demo` → **Custom domains** → **Set up a custom domain** → `demo.yichlin.com`。
   - ⚠️ **現在 `demo.yichlin.com` 已經有東西**：2026-10-13 打開會被導到 Cloudflare Access 的登入頁
     （`vi000246.cloudflareaccess.com`），表示 DNS 已有一筆 `demo`（或 `*.yichlin.com` 萬用記錄）、而且有一個
     Access application 罩住它（可能是 `*.yichlin.com` 的萬用規則）。所以：
     - DNS：yichlin.com 的 DNS 裡如果有明確的 `demo` 記錄（例如指向 tunnel），先刪掉，Pages 才能建它自己的 CNAME；
       只有萬用記錄就不用動，明確記錄會優先。
     - Access：Zero Trust → Access → Applications，找到罩住 `demo.yichlin.com` 的那個 application。
       萬用的就**另外加一個 `demo.yichlin.com` 的 application、policy 設 Bypass / Everyone**；
       專門給 demo 的就直接刪掉。**不要動 `coach.yichlin.com` 的 Access**（`deploy/commercial.md` §3：登入上線前不能拿）。
   - 伺服器版示範（`WKO5COACH_MODE=demo`）之後要上的話，用另一個 hostname，例如 `try.yichlin.com`，兩者不要共用。
4. 驗收（用無痕視窗，避免帶著 Access 的登入 cookie）：
   - `https://demo.yichlin.com/` 直接打開是虛構跑者的總覽，**沒有**經過 Access 登入頁。
   - 上方導覽每一頁點一次（總覽、課表、圖表、週期、活動、路段、賽事計算機）都有內容。Cloudflare Pages 會把
     `overview.html` 轉成 `/overview`，這是正常的（2026-10-13 用同樣的轉址規則在本機跑過 10 頁，沒有錯誤）。
   - 課表改一堂課，重新整理後還在（localStorage）；週期規劃加賽事顯示「唯讀示範」。
   - DevTools → Network：只有 `demo.yichlin.com` 和 cdnjs 的請求。
5. 改連結（這時候才改，新網址還沒通之前不要改成打不開的網址）——在 repo 開一個小改動：
   - `README.md` 開頭「線上示範」那段和英文摘要的網址 → `https://demo.yichlin.com/`，拿掉「repo 轉 private 之前會搬到…」那行；
   - 本文件開頭的網址；
   - `WKO5COACH_DEMO_CTA_URL`（伺服器版示範的「看靜態示範」連結，有設的話）。
   - 程式和頁面裡沒有寫死 `github.io` 的網址（2026-10-13 查過），不用改程式。
6. 轉 private 前最後確認：`deploy/commercial.md` §5b 的表（NAS 不連 GitHub、不用 deploy key；其他 clone 用自己的帳號）。
7. GitHub → repo → **Settings → General → Danger Zone → Change visibility → Private**。
8. 轉完確認：
   - `https://vi000246.github.io/TrailRunCoach/` 變 404（Pages 停用）；`https://demo.yichlin.com/` 照常。
   - 本機 `git fetch` 正常；開一個 Claude 雲端 session 能 clone（GitHub App 要裝在這個 repo 上）。
   - NAS 不用做事；下次部署照 `docs/deploy/wko5coach.deploy.md`「正式環境：NAS」（Mac build）。
   - `gh-pages` 分支可以留著當歷史，也可以刪掉（Settings → Pages 已經沒作用）。

**Cloudflare Pages 免費方案的限制**（官方文件 <https://developers.cloudflare.com/pages/platform/limits/>，2026-10-13 查）：
每個網站 20,000 個檔案（付費方案 100,000）、單檔 25 MiB、每月 500 次建置（`wrangler pages deploy` 直接上傳不經建置）、
每個專案 100 個自訂網域。沒有 `404.html` 時 Pages 當成單頁應用，找不到的網址回首頁（200）；shim 讀到的不是 JSON
就當成「示範版沒有這筆資料」，不會壞。

**2026-10-13 實測一次匯出**（預設選項、`--activities 40`）：

| 項目 | 數字 | Cloudflare 上限 |
|---|---|---|
| 檔案數 | 3,622（10 頁、3,519 個 GET 回應、15 個預先算好的計算） | 20,000（用了 18%） |
| 總大小 | 33.7 MB（JSON 30.9 MB） | 沒有總量限制 |
| 最大的檔 | `static/py/trc_racepower.zip` 409 KB | 25 MiB |
| 時間 | 建資料 229 秒 + 匯出 601 秒（M 系列 Mac） | — |

檔案數只有在 `--activities` 開很大時才可能碰到上限；調大前先看 `export.json` 的 `gets`。

⚠️ 匯出裡的 `static/py/trc_racepower.zip` 是賽事計算機在瀏覽器裡跑的引擎（Pyodide），內容是
`backend/engine/racepower/**`、`workout_steps.py`、`zones.py`、`heat*.py` 等約 50 個 Python 檔的**原始碼**（解壓 1.1 MB）。
它放在公開的示範網站上，任何人都下載得到；repo 轉 private 之後這部分程式還是公開的。現在 GitHub Pages 上的示範也已經有這個檔。

## 3. （選用）Hugging Face 靜態 Space

靜態 Space 免費。開一個 Space（SDK 選 **Static**），把 `dist/static-demo` 的內容放進 Space repo 的根目錄，
再加這份 `README.md`：

```markdown
---
title: TrailRunCoach Demo
emoji: 🏔️
colorFrom: blue
colorTo: yellow
sdk: static
app_file: index.html
pinned: false
short_description: 越野跑／百岳／路跑訓練教練的唯讀示範（虛構跑者）
---
```

`data/` 有上千個小 JSON，用 `git` 推（或 `huggingface-cli upload <space> dist/static-demo . --repo-type space`）。

## 另一條路：伺服器版示範（Docker）

靜態網站是**額外**的發佈方式，不取代伺服器版。伺服器版示範（`WKO5COACH_MODE=demo`：每位訪客一個沙盒，
可以改週期規劃、上傳 GPX，每週自動重建資料）照舊打包成 Docker image：[`deploy/hf/Dockerfile`](../hf/Dockerfile)。
它可以跑在任何 Docker 主機（雲端 VM、Cloud Run、Fly.io…），Hugging Face Docker Space（現在要付費）只是其中一種，
見 [`deploy/hf/`](../hf/README.md)。兩者共用同一套頁面；`shell.js` 只有在頁面載入了靜態 shim 時才切換成唯讀示範的
banner 和鎖定，伺服器版的行為不變。
