# CLAUDE.md

給在這個 repo 工作的 Claude（以及人）。任何一台電腦都一樣。

## 兩個 repo：完整版在 private，公開版只能由同步機制推

- **開發用的是 private repo**：`git@github.com:vi000246/TrailRunCoach-private.git`，本機 remote 叫 `origin`。
  平常的 `git push` 都推到 `origin`。
- **公開 repo `vi000246/TrailRunCoach` 是鏡像**：內容＝private 某個 commit 扣掉計算核心（核心位置放空殼）。
  它的歷史在 2026-10-14 改寫過，跟這個 repo **沒有共同歷史**。
- **絕對不要從這個 repo 推公開 repo**（不管有沒有 `--force`）。推上去就等於把計算核心公開。
  公開版只能用 `deploy/public-mirror/public_mirror.py sync`（推之前會自動檢查沒有核心檔）產生，
  而且由同步機制推，不是從開發 repo 推。細節：`deploy/public-mirror/README.md`。
- **同步是自動的**：GitHub Action `.github/workflows/public-mirror.yml` 在每次 push 到 `origin` 的 main 之後、
  每週一 03:00，以及手動觸發時跑 sync，檢查通過才用 deploy key 推公開 repo。公開 repo 的 main 有 ruleset，
  **只有那把 deploy key 能更新**（擋 force push、擋刪除，連管理員帳號都推不上去）。人不需要、也不應該手動推。
- 這台的 remote 應該長這樣（`git remote -v`）：

  ```
  origin  git@github.com:vi000246/TrailRunCoach-private.git (fetch)
  origin  git@github.com:vi000246/TrailRunCoach-private.git (push)
  public  https://github.com/vi000246/TrailRunCoach.git (fetch)
  public  DISABLED (push)
  ```

  不是這樣（例如 `origin` 還指著公開 repo）就先照 `deploy/public-mirror/README.md` 最後一節「另一台電腦」改，
  改之前不要 push 任何東西。

## 部署

正式站在 NAS（容器 `trailruncoach`），image 在 Mac build、`docker save | ssh | docker load`，不在 NAS 上 build。
步驟與備份規則在 `docs/deploy/`。部署時 push main 到 `origin`，公開鏡像會由 Action 自動同步。
