# Nintendo Game Trend Radar

獨立處理 Nintendo Switch（NS）與 Nintendo Switch 2（NS2）的新作候選、人氣條件及平台別發售資料。公開月曆沿用 `danielet087/game-trend-radar`，Steam 與 Nintendo 的原始人氣數不混成同一種分數。

## 收錄規則

- 每天重新調查未來 365 天的 NS／NS2 原生版本；使用平台別發售記錄，移植版不受遊戲全球首發日期較早影響。
- `hypes ≥ 30` 通過第一版人氣条件；20～29 保留觀察，低值及未知仍留在候選狀態。缺值不當成零。
- 月曆只使用確切年月日；年、季度、月份與未定日期留在候選資料，不能補造發售日。
- 每個平台的日期與地區分開保存。同日雙平台在前端合併顯示；不同日各自出現在對應日期。
- 依可取得的遊戲介紹、類型及分級內容排除以性愛色情為主要內容的作品；單純成人年齡分級、暴力或裸露不直接等同色情。缺少可判讀內容時保留待檢查。
- 月曆標 NS、NS2 或兩者；「獨佔」必須有同款遊戲的 Nintendo 官方證據。IGDB 目前只列單一平台時保留 `listed_only`，不冒充官方獨佔聲明。每日重查所有平台，日後新增平台會更新標示。
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

Cloudflare 的既有控制器每天台灣 **08:30** 派發獨立的 **Collect Nintendo catalog** workflow，原 Cron 字串保持不變。執行入口暫放於已有憑證的 [Twitch 後端 Actions](https://github.com/danielet087/game-trend-radar-twitch-backend/actions/workflows/collect-nintendo.yml)，每次 checkout 本倉庫的最新 `main`。本倉庫只管理 Nintendo 程式與測試，不需複製 Secrets，也沒有 GitHub 原生 cron。

可在上述 workflow 的 **Run workflow** 手動執行。`target_slot` 空白代表實際手動時間；Cloudflare 傳入每日原定 UTC slot。發布前會在 concurrency 內檢查持久回條，避免同一每日 slot 重複執行。第一次部署 workflow 會自動收集一次。

發布器僅更新白名單 Nintendo JSON，合併最新 Git tree，不覆寫 Steam／Twitch 資料。完整候選與回條持久保存在前端 `data/`；同時鏡像到本倉庫 `data/`。若既有 `FRONTEND_REPO_TOKEN` 尚未涵蓋新倉庫的 Contents write 權限，會明示鏡像被阻擋，前端候選與月曆仍可正常更新。日後擴充該 Token 的新倉庫權限即可恢復鏡像，不需要改收集程式。

## 資料限制

IGDB `hypes` 是遊戲記錄的發售前關注數，跨平台不能拆成 Nintendo 玩家人數。NS／NS2 支援與日期由各平台資料決定；向下相容不直接當成另一個原生版本。未有台灣資料時保留實際來源地區，不把其他地區日期標成台灣官方日期。

台灣日期優先使用 `data/nintendo_release_dates.json` 內核對的任天堂台灣／發行商台灣官方日期；每筆綁定 IGDB ID、英文全名、NS／NS2、確切日期、官方網址及核對時間。`--taiwan-releases` 可指定登錄檔，發布器仍從本倉庫的可信登錄檔重新驗證。保留 IGDB 原日期、區域及 Unix 秒，另外稽核 UTC→`Asia/Taipei` 日期；IGDB 日精度不是實際解鎖時刻，不顯示推算的 08:00。未有官方台灣證據且換算跨日時，保留待確認、暫停放入月曆，不能直接替區域日加一天。已核對的 IGDB 原日期若日後改成不同的新日期，撤回旧的台灣確認，避免舊登錄資料蓋住延期；台灣日期及原始 IGDB 記錄分開保存，月曆只使用選中的平台日期。

來源：[IGDB API](https://api-docs.igdb.com/)、各遊戲 Nintendo 官方商品頁。
