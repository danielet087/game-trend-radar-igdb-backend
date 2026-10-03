# Nintendo Game Trend Radar

獨立處理 Nintendo Switch（NS）與 Nintendo Switch 2（NS2）的新作候選、人氣條件及平台別發售資料。公開月曆沿用 `danielet087/game-trend-radar`，Steam 與 Nintendo 的原始人氣數不混成同一種分數。

## 收錄規則

- 每天重新調查未來 365 天的 NS／NS2 原生版本；使用平台別發售記錄，移植版不受遊戲全球首發日期較早影響。
- `hypes ≥ 30` 通過第一版人氣条件；20～29 保留觀察，低值及未知仍留在候選狀態。缺值不當成零。
- 月曆只使用確切年月日；年、季度、月份與未定日期留在候選資料，不能補造發售日。
- 每個平台的日期與地區分開保存。同日雙平台在前端合併顯示；不同日各自出現在對應日期。
- 依可取得的遊戲介紹、類型及分級內容排除以性愛色情為主要內容的作品；單純成人年齡分級、暴力或裸露不直接等同色情。缺少可判讀內容時保留待檢查。
- 月曆標 NS、NS2 或兩者；「獨佔」必須有同款遊戲的 Nintendo 官方證據。IGDB 目前只列單一平台時保留 `listed_only`，不冒充官方獨佔聲明。每日重查所有平台，日後新增平台會更新標示。
- 中文名稱只採用可辨識語區的來源；繁中優先，簡中轉繁，未確認時保留英文。

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

## 自動更新

Cloudflare 的既有控制器每天台灣 **08:30** 派發獨立的 **Collect Nintendo catalog** workflow，原 Cron 字串保持不變。執行入口暫放於已有憑證的 [Twitch 後端 Actions](https://github.com/danielet087/game-trend-radar-twitch-backend/actions/workflows/collect-nintendo.yml)，每次 checkout 本倉庫的最新 `main`。本倉庫只管理 Nintendo 程式與測試，不需複製 Secrets，也沒有 GitHub 原生 cron。

可在上述 workflow 的 **Run workflow** 手動執行。`target_slot` 空白代表實際手動時間；Cloudflare 傳入每日原定 UTC slot。發布前會在 concurrency 內檢查持久回條，避免同一每日 slot 重複執行。第一次部署 workflow 會自動收集一次。

發布器僅更新白名單 Nintendo JSON，合併最新 Git tree，不覆寫 Steam／Twitch 資料。完整候選與回條持久保存在前端 `data/`；同時鏡像到本倉庫 `data/`。若既有 `FRONTEND_REPO_TOKEN` 尚未涵蓋新倉庫的 Contents write 權限，會明示鏡像被阻擋，前端候選與月曆仍可正常更新。日後擴充該 Token 的新倉庫權限即可恢復鏡像，不需要改收集程式。

## 資料限制

IGDB `hypes` 是遊戲記錄的發售前關注數，跨平台不能拆成 Nintendo 玩家人數。NS／NS2 支援與日期由各平台資料決定；向下相容不直接當成另一個原生版本。未有台灣資料時保留實際來源地區，不把其他地區日期標成台灣官方日期。

來源：[IGDB API](https://api-docs.igdb.com/)、各遊戲 Nintendo 官方商品頁。
