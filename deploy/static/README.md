# 靜態示範（GitHub Pages）

公開示範的預設做法：把示範模式匯出成一個**純靜態網站**（HTML + JSON），放在 GitHub Pages，
不需要任何伺服器：<https://vi000246.github.io/TrailRunCoach/>

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
python -m playwright install chromium               # 匯出用的無頭瀏覽器（只要裝一次）
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
- repo 改成 private 的話，免費方案的 GitHub Pages 會停用。

（替代做法：在 GitHub Actions 用 `actions/upload-pages-artifact` + `actions/deploy-pages` 部署一個預先建好、
放在 Release 附件裡的 zip。需要多維護一個 workflow 和一個 Release，目前不需要。）

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
