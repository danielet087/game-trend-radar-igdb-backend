# IGDB Game Trend Radar

儲存庫：`danielet087/game-trend-radar-igdb-backend`。目前處理 Nintendo Switch（NS）、Nintendo Switch 2（NS2）與 PlayStation 5（PS5）的新作候選、人氣條件及平台別發售資料。公開月曆沿用 `danielet087/game-trend-radar`，Steam 與 IGDB 的原始人氣數不混成同一種分數。

## Turso 主庫模式

實際執行流程位於 Twitch 後端。該儲存庫變數 `RADAR_DATABASE_PRIMARY=1` 時，
每日回條與完整候選均從 Turso 主庫讀取；收集後先以版本檢查原子保存 master、
upcoming 與 `persisted` 回條，再由共用發布器輸出主庫完整只讀快照。
`persisted` 只代表主庫保存成功；Git 交付成功另有實際 commit 回條，失敗留在待發布佇列，
不重複執行已完成的每日收集。
主庫失敗不會改讀 Git 或以空候選重新開始。原始證據與壓縮主檔完整保存，
人工核對的名稱／語言／商品登錄檔繼續作為 Git 版本化設定。
`RADAR_DATABASE_MIGRATION_PAUSE=1` 可於切換期間暫停實際執行 workflow。
下文的前端持久化與後端鏡像行為，僅適用主庫模式尚未啟用的相容路徑。

## 收錄規則

- 每天重新調查未來 365 天的 NS／NS2／PS5 原生版本；使用平台別發售記錄，移植版不受遊戲全球首發日期較早影響。
- 發售後跟 Steam 一樣一直留在公開清單：2026-10-04（第一份公開主機月曆）以後的確切發售日都保留，未來 365 天只決定新加入哪些日期。更早的發售日從未公開，不補回。
- `hypes ≥ 30` 通過第一版人氣条件；20～29 保留觀察，低值及未知仍留在候選狀態。缺值不當成零。
- NS／NS2 另看 IGDB PopScore（`nintendo_backend/popularity.py`）：IGDB 頁面瀏覽（Visits）或「想玩」（Want to Play）任一項排進 NS／NS2 遊戲前 20%，也算通過人氣條件。比較範圍是 90 天前到一年後有確切 NS／NS2 發售日的正式遊戲（DLC、取消作不算）；PopScore 是 IGDB 每天重算的占比，所以看排名、不看固定數字。通過後一直保留（主資料的 `popscore_admitted_at`），之後排名下降也不會消失；只放寬 hypes 這一條，日期、內容與類型條件照舊。PS5 只看 hypes。
- 公開資料的 `popularity_basis` 標示上榜依據（`hypes`／`igdb_popscore`），`popscore` 保存兩項數值與 `top_percent`（較好那項的排名，前 X%；不在比較範圍時為 `null`）。`source.popscore` 記錄比較範圍、門檻與上榜數，`nintendo_refresh_status.json.popscore_fetch` 記錄當天取得狀況；IGDB 當天取不到或回空時沿用前次保存的數值（`previous_values`），發布器也用保存的數值重算比對。
- 月曆只使用確切年月日；年、季度、月份與未定日期留在候選資料，不能補造發售日。
- 每個平台的日期與地區分開保存。同日雙平台在前端合併顯示；不同日各自出現在對應日期。
- 依可取得的遊戲介紹、類型及分級內容排除以性愛色情為主要內容的作品；單純成人年齡分級、暴力、裸露或分級描述中的 `Strong Sexual Content`／`sex scenes` 不直接等同色情；需同時有 Erotic 題材與明確介紹或分級證據才自動排除。只有單一色情線索或缺少可判讀內容時保留待檢查。
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

資料庫主庫模式下，`nintendo_master.json` 搬往不投影到 Git、也不上網站的資料庫專用來源 `danielet087/game-trend-radar-igdb-backend@nintendo-state`（`scripts/state_store.py`）。前端來源還有這個檔時，它仍是權威，每輪照舊與公開檔一起發布，再把相同位元組鏡像到專用來源（鏡像失敗只警告）；backend 的 `scripts/move_nintendo_state.py retire` 確認三份（專用來源、前端資料庫、前端 Git）相同後移除前端這份，之後每輪先把候選主資料寫進專用來源（不可比已存的舊、保留列必須未變），再發布兩個公開檔。`restore` 可放回前端。Git 模式讀不到候選主資料時一律失敗，不會當成第一次執行。

HTTP 成功但缺少遊戲時，先只補查缺失 ID（小批次，再逐筆），整輪最多 20 次補查 query，
仍受原有節流與時間上限約束。補查後仍缺少、但已有完整歷史且原本不在公開月曆的候選，
可在主庫模式保留整筆舊資料及原 `checked_at`；不更換 ID、不補造資料，也不重新標成今天已查。
下一輪仍會查詢該 ID。公開過的候選、全新 ID、HTTP 錯誤、重複或非預期 ID、格式錯誤不適用此例外。

這類收集明示 `complete=false`、`lookup_complete=false`、`ledger_complete=true` 與
`public_complete=true`：完整候選仍保留，這次公開的每筆遊戲都已重新驗證，但歷史候選尚有查詢缺項。
`source.game_lookup.unavailable_ids` 與 `unavailable` 保存缺失 ID、最後成功時間與查詢次數；
本次嘗試時間沿用回條的 `generated_at`。
主庫發布前會再次與最新候選及公開清單比對，確認保留資料完全相同且原本未公開，
才可交付其他正常資料；回條使用 `persisted_partial`／`published_partial`，不冒稱全部候選已更新。
每日排程仍在隔天重試；不新增密集重試。失敗狀態檔亦保留為 workflow artifact，方便查明是哪筆資料出問題。

既有每日收集流程同時處理中文名稱，不增加另一個排程。`--chinese-names` 可指定經核對的名稱登錄檔（預設 `data/chinese_names.json`）；這是人工驗證的官方名稱來源，不會自動爬取任天堂或發行商網站。每筆格式為 `igdb_id`、`name_en`、`name_zh_tw`、`source`、`url`，外層是 `schema_version: 1` 與以 `igdb:<ID>` 為鍵的 `games`。

只有通過月曆資格且尚無已驗證繁中名的遊戲才會查 Steam 名稱備援。必須有 IGDB 明示 Steam 服務的 AppID 或官方 Steam 商品網址；矛盾 AppID 不採用。先查官方 `appdetails` 繁中，再查簡中轉繁；檢查回傳 AppID、正式遊戲類型與實際中文名稱，不能因請求繁中仍回英文／日文就宣告成功。每輪最多 30 次串行請求、90 秒，遇 429 停止，網路失敗保留已有中文名。候選保存名稱來源、語區、身分證據與嘗試時間，下輪優先處理未嘗試或較久未查的遊戲，避免請求額度一直卡在前幾款。這條備援只補名稱，不使用 Steam 的平台、發售日期、Followers 或內容資格。

## 中文簡介與後續更新

公開資料提供 `short_description`、`short_description_en`、`short_description_language`、`short_description_source`、`description_source_url`。官方中文優先；沒有中文時直接顯示 IGDB 英文原文，不自動翻譯。IGDB 優先使用 `summary`，空白時改用 `storyline`；英文同時保存於 `short_description_en`，日文不冒充英文。官方繁中原文保留原字與專名，不再轉字；中文簡體字透過 OpenCC `s2tw` 轉為繁體，僅進行字形轉換。這些欄位只負責介紹，不改變遊戲內支援語言、日期、平台或收錄資格。

簡介階段只處理已符合公開資格的遊戲。官方中文只調查既有發售、語言與名稱登錄檔已綁定同款商品的 Nintendo／PlayStation 台港精確商品網址，以及 PS5 遊戲在 IGDB 上自己連結的 PlayStation Store 商品或遊戲頁（改讀台灣繁中商店 `zh-hant-tw` 的同一個 ID，排在已核對商品之後），不搜尋全網，不把公告清單、豪華版或另一款遊戲的描述套用進來。PlayStation Store 頁以該 ID 本身的商品／遊戲資料為準：Sony 的英文固定名稱（invariantName）須與 IGDB 名稱或別名相符（續作羅馬數字與阿拉伯數字視為相同），英文顯示名稱也須相符，頁面列出平台時須含 PS5，商品須為標準版；商店介紹去掉試玩、購買、版本、特典、訂閱、語言支援、版權、網址等商店附註，商店自稱機器翻譯、不足 10 個中文字或含日文假名的介紹不採用，簡體字介紹以 OpenCC `s2tw` 轉繁並標「官方簡中・轉繁」。同一頁的中文遊戲名稱（去掉 ™、語言與平台標示；「英文 (中文)」取中文）只在遊戲還沒有自己的繁中名稱時補上，名稱來源標「PlayStation Store 台灣」，IGDB 不再連結該商品時不沿用。PS5 版還沒有台港商店連結時，這個已核對為同款遊戲的台灣商品頁也作為 PS5 版的商店連結（IGDB 通常只連到美國商店頁）；簡介不再採用該頁時連結也一併移除。Nintendo 已核對的 14 位商品 ID 會先查登錄地區，再以相同商品 ID 查另一個台灣／香港中文商品頁，避免台灣頁尚未提供時漏掉既有香港中文。商品標題仍須匹配已核對名稱；頁面明列主機時也不能與 NS／NS2／PS5 版本矛盾。使用商品頁 JSON-LD 的完整介紹，不能只因搜尋結果有中文就當成已抓取成功。只有商品身分與實際中文內容都符合才可升級為官方簡介；已知對應以外的主機遊戲使用 IGDB 原文。官方商品文案出現中文與遊戲內新增中文支援是兩件事。

官方中文獨立到期檢查，不會因已有英文介紹或英文未變而永久跳過：還沒有官方中文的遊戲在上市前每 3 天查一次，上市後只要已用目前的網址查過就不再查（第一次出現就已上市的遊戲仍會查一次）；已有官方中文每週查。已核對的官方文案、商品身分及嘗試時間保存在完整主資料，沿用既有三檔發布流程持久保存。每輪最多 60 次官方請求、150 秒，遇限流停止，較久未查的作品下輪優先；暫時抓取失敗不清空已核實的官方中文。舊機器翻譯不作為中文來源。

`nintendo_refresh_status.json.description_enrichment` 顯示官方中文筆數、請求與失敗數、限流狀態、英文備援數及無介紹數。流程不依賴翻譯 API、翻譯服務金鑰或本地翻譯模型。

## 自動更新

Cloudflare 的既有控制器每天台灣 **08:30** 派發獨立的 **Collect IGDB console catalog** workflow，原 Cron 字串保持不變。執行入口暫放於已有憑證的 [Twitch 後端 Actions](https://github.com/danielet087/game-trend-radar-twitch-backend/actions/workflows/collect-nintendo.yml)，每次 checkout 本倉庫的最新 `main`。本倉庫管理 IGDB 主機收集程式與測試，不需複製 Secrets，也沒有 GitHub 原生 cron。

可在上述 workflow 的 **Run workflow** 手動執行。`target_slot` 空白代表實際手動時間；Cloudflare 傳入每日原定 UTC slot。發布前會在 concurrency 內檢查持久回條，避免同一每日 slot 重複執行。第一次部署 workflow 會自動收集一次。

發布器僅更新白名單 IGDB 主機 JSON，合併最新 Git tree，不覆寫 Steam／Twitch 資料。完整候選與回條持久保存在前端 `data/`；同時鏡像到本倉庫 `data/`。若既有 `FRONTEND_REPO_TOKEN` 尚未涵蓋新倉庫的 Contents write 權限，會明示鏡像被阻擋，前端候選與月曆仍可正常更新。日後擴充該 Token 的新倉庫權限即可恢復鏡像，不需要改收集程式。

## 資料限制

IGDB `hypes` 是遊戲記錄的發售前關注數，跨平台不能拆成單一主機玩家人數。NS／NS2／PS5 支援與日期由各平台資料決定；向下相容不直接當成另一個原生版本。未有台灣資料時保留實際來源地區，不把其他地區日期標成台灣官方日期。

同一收集流程另讀取 IGDB `game_modes` 與 `multiplayer_modes`。公開資料保留遊戲模式的 ID／名稱，以及各多人模式原有的平台 ID、合作／分割畫面布林欄位與最大人數；只有型別正確的欄位可發布，缺少資料維持未知。這些證據供卡片的「多人」標示使用，不從遊戲名稱、分類、題材或摘要推定多人，也不把某平台的多人設定套用到其他平台。每次發布從完整 IGDB 原始資料重建比對，防止公開清單自行加上模式證據。

同一次讀取也帶回遊戲頁的資料：IGDB 自己的類型（`igdb_genres`）與題材（`igdb_themes`，不含只用於成人內容判斷的 Erotic）、開發商與發行商（`involved_companies` 中標為 developer／publisher 的公司）、系列（`franchises`），以及官方截圖、橫幅美術與預告（`media`：只留 IGDB 圖片 ID 與 YouTube 影片 ID，網址由網站組出，不會出現其他網域）。名稱保留 IGDB 原文，中文由網站對照；每份清單都有上限（類型、題材各 6、公司各 4、系列 2、截圖 8、美術 3、預告 3），因為主機清單檔每個月曆頁都會讀取。主檔保存的 IGDB 原始資料也只留到這些上限。

Nintendo 支援語言另由 `data/nintendo_languages.json` 保存已核對的官方版本資料。每筆同時綁定 IGDB ID、英文全名與 NS／NS2，並保存來源地區、官方網址、完整語言清單及實際核對時間；任天堂台灣商品資料優先。Steam 語言、IGDB 全遊戲語言、另一個平台版本不能填入 Nintendo 版本。若官方商品是本體加 DLC、Deluxe 等不同名稱版本，官方描述明確包含同一款本體，且原生平台與商品身分均已核對，就可登錄該商品的語言表，並保存官方商品名稱、商品 ID 及身分對應證據；獨立 DLC、其他作品與 Cloud 版仍需分別核對。香港或其他官區的資料保留來源地區，不據此宣稱台灣版本。官方只寫「Chinese／中文」時只確認中文，繁／簡仍為未知；只有完整官方清單才能將未列出的語言標為不支援。

既有收集流程載入這份登錄檔（`--nintendo-languages`），公開 `platform_language_support.NS／NS2`，發布前從本倉庫的可信登錄檔重建比對，防止公開清單自行宣稱確認。缺少證據的版本保留 `unknown` 與 `null`，不當成不支援；核對時間不會隨 IGDB 每日收集改成今天。這份登錄檔是已人工調查的官方快照，既有排程重用已核對資料，尚未自動重新爬取官方語言清單。新版本與日後的官方變更需更新登錄檔；未解項目的調查紀錄在 `data/nintendo_language_investigations.json`，不作為語言支援證據。

已核對的綑綁／Deluxe 商品另公開 `platform_editions.NS／NS2`，讓前端明示「本體＋擴充版」、「Deluxe 版」或「本體＋DLC」。登錄檔需明示 `edition_type`、`edition_label`、官方商品名稱與商品 ID，並提供 `identity_relation: base_game_included`、本體包含證據及官方證據網址；語言與版本標示分開保存。公開版本資料包含商品名稱、地區、商品網址及原核對時間，同樣由發布 gate 重新驗證。缺少已核對版本資料時不產生版本標示，不推定為普通本體版；日後原生 NS／NS2 對應改變或官方更改內容需重新核對。

NS、NS2、PS5 的月曆發售日期採用 IGDB 的平台別發售記錄；PS5 版有台灣 PlayStation Store 目前確認的發售時間時，改用台灣商店的日期（見下方「PS5 台灣商店資料」）。IGDB 確切日的 Unix timestamp 轉為 `Asia/Taipei` 後，使用換算出的台灣時區日期；同時保存原 IGDB 日曆日、Unix 秒、來源地區與換算結果。跨日換算也使用轉換後日期，但不宣稱這是台灣官方發售確認。只有年月日而沒有 timestamp 時保留該日期，不補造時刻；年、季、月及未定仍不能進入確切日期月曆。

公開資料的日期政策為 `source.release_date_policy: taiwan_store_then_igdb_taipei_v1`（台灣商店的日期優先，其次是 IGDB 換算台灣時區）。有 timestamp 的日精度記錄使用 `date_basis: igdb_timestamp_taipei` 與 `timezone_status: converted_to_taipei`；只有日期的記錄使用 `igdb_calendar_day` 與 `date_only`。詳細頁標示 IGDB 與台灣時區，不將資料庫預設午夜換算的 08:00 當成實際解鎖時刻。平台選日仍按來源地區優先級（台灣、亞洲、全球、日本、中國、韓國、其他），相同優先級互相矛盾的日期保留候選、等待 IGDB 更正。

`data/nintendo_release_dates.json`／`data/playstation_release_dates.json` 保留歷史官方證據和已核對商品連結；`--taiwan-releases`／`--playstation-releases` 沿用作相容與商店連結來源，**不覆蓋月曆日期**：登錄檔是某一天的快照，日期改了也不會知道；會再回查的台灣商店資料才會蓋過 IGDB。中文名稱、各平台官方支援語言與版本商品身分仍使用各自已核對的登錄檔，Steam 日期流程維持原規則。

來源：[IGDB API](https://api-docs.igdb.com/)、各遊戲 Nintendo 官方商品頁。

## PS5 官方版本證據

PS5（IGDB 平台 ID `167`）沿用每日 08:30 同一收集器、hypes ≥ 30、未來 365 天與確切日／內容資格規則。保留既有 `nintendo_backend` 模組與三個 `nintendo_*.json` 傳輸檔名以相容候選歷史、收藏與跑馬燈；檔案現在涵蓋 NS、NS2、PS5，來源均是 IGDB。PS4 的向下相容不能生成 PS5 原生版本。

`data/playstation_release_dates.json` 與 `data/playstation_languages.json` 保存已核對的 Sony 官方 PS5 版本證據，分別以 `--playstation-releases`、`--playstation-languages` 載入；前者只提供已核對商店連結，不改日期。語言與版本資料仍由發布 gate 核對 IGDB ID、英文完整名稱、原生平台、商品身分與官方來源，香港語言資料保留香港標示。PS5 不沿用 Steam 或 NS／NS2 的語言與版本證據。

## PS5 台灣商店資料

每天的收集對已公開、有 PS5 版本、而且到期的遊戲查 PlayStation Store 台灣商店（`official_store`）：
- 只查 IGDB 自己連到的商品或 concept，改讀台灣商店的同一個 ID；頁面選定的商品要是同一款遊戲（英文完整名稱）、PS5 原生版、正式本體與標準版，不搜尋猜測商品，也不把 Deluxe／DLC 當成本體。concept 要有頁面顯示的 UTC 發售時刻。
- 確認到的資料直接採用，排在 IGDB 前面：
  - 發售時間換算台灣時區當作 PS5 版的日期（`source: official_registry`、`region: taiwan`、`taiwan_release_confirmed: true`、`official_release_time_utc`）；IGDB 自己的日期與時間戳留在 `source_date`／`source_timestamp`，網站可以說明「原始 IGDB 日期為…，已依台灣官方日期修正」。`release_records` 仍是 IGDB 原始記錄。
  - PS5 語言：台灣商店的語言表（完整清單標 confirmed，只有標題標示的標 partial）；已核對登錄檔有完整清單、商店只有部分時，保留登錄檔。
  - PS5 沒有其他商店連結時，台灣商品頁就是 PS5 的商店連結。
- 查詢時間表（每款各自計算）：還沒確認過的先查；上市前每 3 天一次，上市前 14 天內每天一次，上市日（或之後）再查最後一次就停。日期以網站目前顯示的 PS5 日期為準，台灣商店改日期就跟著改。IGDB 連到的商品換了就馬上重查。
- 商店有回答（確認、或商店沒有這個商品／頁面是別款遊戲）才算查過；伺服器錯誤、逾時、額度用完或限流不算，隔天再試，原本確認的資料保留。商店不再有這個商品就移除原本的資料，改回 IGDB。
- 確認過的資料在 14 天內有效；上市日當天或之後確認的，之後一直有效。超過 14 天沒有再確認（例如連續查不到），就改回 IGDB 的日期。
- 每輪最多 40 頁、120 秒，遇到 429 整輪停；台灣查不到時另查香港，香港結果只當核對材料，不採用。
- 每款的查詢狀態與確認資料存在完整主資料的 `official_store`（不公開）；發布 gate 用主資料裡的這份狀態重建，日期、語言、連結對不上就整批不發布。`nintendo_refresh_status.json.playstation_investigation` 記錄這輪到期與實際查詢的筆數、各結果數、採用台灣日期與語言的遊戲數。

完整候選量較大時，發布器會將 `nintendo_master.json` 保存為 `gzip-base64` 傳輸封裝（含 SHA-256 與解壓後位元組數）。候選、原始證據、平台歷史與已查狀態全部保留；下一輪 `load_existing` 校驗及解碼後使用相同完整資料。小型舊 JSON 仍可直接載入；公開 `nintendo_upcoming.json` 與回條保持可讀 JSON。封裝不是截斷或刪除候選，也不改變日期／語言發布驗證。
