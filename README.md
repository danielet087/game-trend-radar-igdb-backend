# IGDB Game Trend Radar

儲存庫：`danielet087/game-trend-radar-igdb-backend`。目前處理 Nintendo Switch（NS）、Nintendo Switch 2（NS2）與 PlayStation 5（PS5）的新作候選、人氣條件及平台別發售資料。公開月曆沿用 `danielet087/game-trend-radar`，Steam 與 IGDB 的原始人氣數不混成同一種分數。

## 收錄規則

- 每天重新調查未來 365 天的 NS／NS2／PS5 原生版本；使用平台別發售記錄，移植版不受遊戲全球首發日期較早影響。
- `hypes ≥ 30` 通過第一版人氣条件；20～29 保留觀察，低值及未知仍留在候選狀態。缺值不當成零。
- 月曆只使用確切年月日；年、季度、月份與未定日期留在候選資料，不能補造發售日。
- 每個平台的日期與地區分開保存。同日雙平台在前端合併顯示；不同日各自出現在對應日期。
- 依可取得的遊戲介紹、類型及分級內容排除以性愛色情為主要內容的作品；單純成人年齡分級、暴力或裸露不直接等同色情。缺少可判讀內容時保留待檢查。
- 卡片分為 PC＋主機、主機多平台、Steam 與主機；僅有同款遊戲的平台官方獨佔證據才顯示獨佔。NS／NS2 獨佔用紅色，PS5 獨佔用藍色。IGDB 目前只列單一平台時保留 `listed_only`，不冒充官方獨佔聲明。每日重查所有平台，日後新增平台會更新標示。
- 中文名稱優先使用 `data/chinese_names.json` 內已核對的官方繁中名稱，其次 IGDB 繁中名稱／明確標示中文的別名與先前已驗證名稱；簡中轉繁，未確認時保留英文。登錄資料同時綁定 IGDB ID 與英文全名，避免重製版、移植版、Cloud 版誤用名稱。

## 執行與資料

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m nintendo_backend.collect --output-dir output
python -m nintendo_backend.exclusivity --output-dir output
```

收集環境使用 `TWITCH_CLIENT_ID`／`TWITCH_CLIENT_SECRET`；API 使用 Twitch app access token。憑證只在 runner 使用，不存進 JSON、原始碼或日誌。失敗不發布不完整 catalog；429 有限退避後仍失敗就保留前次公開資料。

| 檔案 | 用途 |
| --- | --- |
| `nintendo_master.json` | 全部候選、平台／日期／內容證據、觀察狀態及更新歷史 |
| `nintendo_upcoming.json` | 通過資格且有確切日期的前端遊戲與平台別上市日期 |
| `nintendo_refresh_status.json` | 本輪完整性、數量、實際收集時間與發布回條 |

`--existing input/nintendo_master.json` 載入持久候選，重新查詢既有遊戲的平台與日期。`id` 使用 `igdb:<ID>`，不與 Steam app ID 衝突。

既有每日收集流程同時處理中文名稱，不增加另一個排程。`--chinese-names` 可指定經核對的名稱登錄檔（預設 `data/chinese_names.json`）；這是人工驗證的官方名稱來源，不會自動爬取任天堂或發行商網站。每筆格式為 `igdb_id`、`name_en`、`name_zh_tw`、`source`、`url`，外層是 `schema_version: 1` 與以 `igdb:<ID>` 為鍵的 `games`。

只有通過月曆資格且尚無已驗證繁中名的遊戲才會查 Steam 名稱備援。必須有 IGDB 明示 Steam 服務的 AppID 或官方 Steam 商品網址；矛盾 AppID 不採用。先查官方 `appdetails` 繁中，再查簡中轉繁；檢查回傳 AppID、正式遊戲類型與實際中文名稱，不能因請求繁中仍回英文／日文就宣告成功。每輪最多 30 次串行請求、90 秒，遇 429 停止，網路失敗保留已有中文名。候選保存名稱來源、語區、身分證據與嘗試時間，下輪優先處理未嘗試或較久未查的遊戲，避免請求額度一直卡在前幾款。這條備援只補名稱，不使用 Steam 的平台、發售日期、Followers 或內容資格。

## 自動更新

Cloudflare 的既有控制器每天台灣 **08:30** 派發獨立的 **Collect IGDB console catalog** workflow，原 Cron 字串保持不變。執行入口暫放於已有憑證的 [Twitch 後端 Actions](https://github.com/danielet087/game-trend-radar-twitch-backend/actions/workflows/collect-nintendo.yml)，每次 checkout 本倉庫的最新 `main`。本倉庫管理 IGDB 主機收集程式與測試，不需複製 Secrets，也沒有 GitHub 原生 cron。

可在上述 workflow 的 **Run workflow** 手動執行。`target_slot` 空白代表實際手動時間；Cloudflare 傳入每日原定 UTC slot。發布前會在 concurrency 內檢查持久回條，避免同一每日 slot 重複執行。第一次部署 workflow 會自動收集一次。

發布器僅更新白名單 IGDB 主機 JSON，合併最新 Git tree，不覆寫 Steam／Twitch 資料。完整候選與回條持久保存在前端 `data/`；同時鏡像到本倉庫 `data/`。若既有 `FRONTEND_REPO_TOKEN` 尚未涵蓋新倉庫的 Contents write 權限，會明示鏡像被阻擋，前端候選與月曆仍可正常更新。日後擴充該 Token 的新倉庫權限即可恢復鏡像，不需要改收集程式。

## 資料限制

IGDB `hypes` 是遊戲記錄的發售前關注數，跨平台不能拆成單一主機玩家人數。NS／NS2／PS5 支援與日期由各平台資料決定；向下相容不直接當成另一個原生版本。未有台灣資料時保留實際來源地區，不把其他地區日期標成台灣官方日期。

Nintendo 支援語言另由 `data/nintendo_languages.json` 保存已核對的官方版本資料。每筆同時綁定 IGDB ID、英文全名與 NS／NS2，並保存來源地區、官方網址、完整語言清單及實際核對時間；任天堂台灣商品資料優先。Steam 語言、IGDB 全遊戲語言、另一個平台版本不能填入 Nintendo 版本。若官方商品是本體加 DLC、Deluxe 等不同名稱版本，官方描述明確包含同一款本體，且原生平台與商品身分均已核對，就可登錄該商品的語言表，並保存官方商品名稱、商品 ID 及身分對應證據；獨立 DLC、其他作品與 Cloud 版仍需分別核對。香港或其他官區的資料保留來源地區，不據此宣稱台灣版本。官方只寫「Chinese／中文」時只確認中文，繁／簡仍為未知；只有完整官方清單才能將未列出的語言標為不支援。

既有收集流程載入這份登錄檔（`--nintendo-languages`），公開 `platform_language_support.NS／NS2`，發布前從本倉庫的可信登錄檔重建比對，防止公開清單自行宣稱確認。缺少證據的版本保留 `unknown` 與 `null`，不當成不支援；核對時間不會隨 IGDB 每日收集改成今天。這份登錄檔是已人工調查的官方快照，既有排程重用已核對資料，尚未自動重新爬取官方語言清單。新版本與日後的官方變更需更新登錄檔；未解項目的調查紀錄在 `data/nintendo_language_investigations.json`，不作為語言支援證據。

已核對的綑綁／Deluxe 商品另公開 `platform_editions.NS／NS2`，讓前端明示「本體＋擴充版」、「Deluxe 版」或「本體＋DLC」。登錄檔需明示 `edition_type`、`edition_label`、官方商品名稱與商品 ID，並提供 `identity_relation: base_game_included`、本體包含證據及官方證據網址；語言與版本標示分開保存。公開版本資料包含商品名稱、地區、商品網址及原核對時間，同樣由發布 gate 重新驗證。缺少已核對版本資料時不產生版本標示，不推定為普通本體版；日後原生 NS／NS2 對應改變或官方更改內容需重新核對。

NS、NS2、PS5 的月曆發售日期統一採用 IGDB 的平台別發售記錄。確切日的 Unix timestamp 轉為 `Asia/Taipei` 後，使用換算出的台灣時區日期；同時保存原 IGDB 日曆日、Unix 秒、來源地區與換算結果。跨日換算也使用轉換後日期，但不宣稱這是台灣官方發售確認。只有年月日而沒有 timestamp 時保留該日期，不補造時刻；年、季、月及未定仍不能進入確切日期月曆。

公開資料的日期政策為 `source.release_date_policy: igdb_taipei_v1`。有 timestamp 的日精度記錄使用 `date_basis: igdb_timestamp_taipei` 與 `timezone_status: converted_to_taipei`；只有日期的記錄使用 `igdb_calendar_day` 與 `date_only`。詳細頁標示 IGDB 與台灣時區，不將資料庫預設午夜換算的 08:00 當成實際解鎖時刻。平台選日仍按來源地區優先級（台灣、亞洲、全球、日本、中國、韓國、其他），相同優先級互相矛盾的日期保留候選、等待 IGDB 更正。

`data/nintendo_release_dates.json`／`data/playstation_release_dates.json` 保留歷史官方證據和已核對商品連結；`--taiwan-releases`／`--playstation-releases` 沿用作相容與商店連結來源，**不再覆蓋 IGDB 月曆日期**。中文名稱、各平台官方支援語言與版本商品身分仍使用各自已核對的登錄檔，Steam 日期流程維持原規則。

來源：[IGDB API](https://api-docs.igdb.com/)、各遊戲 Nintendo 官方商品頁。

## PS5 官方版本證據

PS5（IGDB 平台 ID `167`）沿用每日 08:30 同一收集器、hypes ≥ 30、未來 365 天與確切日／內容資格規則。保留既有 `nintendo_backend` 模組與三個 `nintendo_*.json` 傳輸檔名以相容候選歷史、收藏與跑馬燈；檔案現在涵蓋 NS、NS2、PS5，來源均是 IGDB。PS4 的向下相容不能生成 PS5 原生版本。

`data/playstation_release_dates.json` 與 `data/playstation_languages.json` 保存已核對的 Sony 官方 PS5 版本證據，分別以 `--playstation-releases`、`--playstation-languages` 載入；前者只提供已核對商店連結，PS5 日期統一依上述 IGDB 台灣時區政策。語言與版本資料仍由發布 gate 核對 IGDB ID、英文完整名稱、原生平台、商品身分與官方來源，香港語言資料保留香港標示。PS5 不沿用 Steam 或 NS／NS2 的語言與版本證據。

既有流程對符合月曆資格且有 PS5 版本的遊戲進行有上限的官方 Store 調查（最多 30 頁、90 秒，台灣优先、香港備援），優先未來 PS5 版本，再查尚未嘗試或較久未查的版本；同等條件按 hypes 排序，避免低順位一直耗盡額度。調查進度保存在完整候選主資料，下一輪沿用。只使用 IGDB 明示的商品／concept URL，檢查頁面選定商品、英文完整名稱、PS5 與正式本體類型；不搜尋猜測商品，也不把 Deluxe／DLC 自動當成本體。`nintendo_refresh_status.json.playstation_investigation` 明示 `review_only: true`，供後續核對；調查報告不直接改變公開日期或語言，語言與版本的正式確認仍須更新可信登錄檔；日期只由 IGDB 更新。商品頁未公開語言、查不到或遇 429 時，保留未知與調查狀態。

Sony 官方商品與 concept 調查報告保留為版本、連結與語言核對材料，內含的官方日期／時刻不再覆蓋月曆日期。IGDB 每日更新的平台日期直接按台灣時區顯示。

完整候選量較大時，發布器會將 `nintendo_master.json` 保存為 `gzip-base64` 傳輸封裝（含 SHA-256 與解壓後位元組數）。候選、原始證據、平台歷史與已查狀態全部保留；下一輪 `load_existing` 校驗及解碼後使用相同完整資料。小型舊 JSON 仍可直接載入；公開 `nintendo_upcoming.json` 與回條保持可讀 JSON。封裝不是截斷或刪除候選，也不改變日期／語言發布驗證。
