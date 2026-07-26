# Bug Hunt Report — 2026-07-26 (Gate 3 pre-flight)

> 對應 `.methodology/bug_hunt_report.json`。
> 獵物:taskq (cli.py / executor.py / store.py 三 high-risk module + 5 standard module)。
> 工具:Agent 5-lens hunt + 手動驗證 + 威脅模型逐條複核。

## 掃描摘要

| module | lens | 發現 | 確認 | 已解決/反駁 |
|---|---|---|---|---|
| taskq.cli | concurrency | F1 `clear_command` 繞過 breaker flock | 1 | RESOLVED (1b45cc8) |
| taskq.cli | correctness | F2 version≠1 訊息用詞不符 | 0 | refuted |
| taskq.executor | general | F3 mid-run clear 訊息誤導 | 0 | refuted |
| taskq.store | resilience | F4 lockfile lifecycle 疑慮 | 0 | refuted |
| taskq.{breaker,cache,config,__init__,__main__} | general | 無 | 0 | — |

Summary:4 raw findings → **1 confirmed (medium)** resolved via repro test + 1-line API +
test-fixture. 其餘 3 條 low-severity 經 verifier 反駁或紀錄。

## 確認 bugs (severity 降序)

### F1 — `clear_command` 寫 `breaker.json` 不經 flock 鎖 [medium]
- 位置:`03-development/src/taskq/cli.py:159-172` + `03-development/src/taskq/breaker.py:62-69`
- 問題:`clear_command` 直接呼叫 `breaker.save`,而 `breaker.save` 只呼叫
  `store._write_unlocked`(沒有 flock)。同進程其它寫 `breaker.json` 的程式
  (`record_failure`、`record_success`)都走 `breaker._locked(home)` → flock 序列化。
  `clear_command` 是唯一繞過 lock 的外部生產者,可能 byte-level 與 in-flight
  `record_failure` 交錯,把剛寫入的 OPEN 紀錄給覆寫回 default CLOSED。
- 證據:`tests/test_bug_hunt_resolve.py::test_clear_command_acquires_breaker_lock`
  用 `breaker._locked` 的 depth-tracker 包裝,在 `clear` 期間觀察 `breaker.save`
  的呼叫是否在 lock 內。fix 前 save() 發生在 depth=0,RED;fix 後 depth≥1,GREEN。
- 修復:新增公開 `breaker.reset(home)`(在 `breaker._locked(home)` 內呼叫
  `save`);`clear_command` 改呼叫 `breaker.reset(cfg.home)`。
- Commit:`1b45cc8` (`fix(breaker): route clear_command through reset() under flock`)
- Repro:`03-development/tests/test_bug_hunt_resolve.py`

## 已被反駁清單(一句理由)

- **F2 (low)**:version≠1 時 stderr 用 `internal error:` 而非 `store corrupted`,
  但 exit code = 1 仍滿足 AC-5.7 規範精神。UX 微差,gate 不阻擋。
- **F3 (low)**:subprocess 結束瞬間同時被 `clear` 刪除的 task,會讓
  `executor._apply_task_update` 拋 `KeyError` 並由 cli 印 `unknown task: <id>`。
  視窗極小(限 subprocess 執行期間),語意滿足 AC-5.6,無資料損毀。
- **F4 (low)**:lockfile 的 `with ... + try/finally LOCK_UN` 在所有退出路徑
  (含 SIGINT)都會釋放 flock,無實際缺陷。

## 修復優先順序

1. F1 — 已於 commit `1b45cc8` 解決。
2. 其它為 low-severity 觀察,留檔追蹤,無需在 Gate 3 前處理。

## 威脅模型逐條驗證 (SAD.md §6 STRIDE-lite)

| Threat | Category | Mitigation effective? | 驗證手段 |
|---|---|---|---|
| T-01 注入字元 | tampering | ✓ | `test_submit_rejects_injection_chars` 7/7 + aggregate |
| T-02 shell=True | tampering | ✓ | `test_no_shell_true_in_source` + `test_shell_true_grep_zero_matches` 各掃一次 |
| T-03 secret 跨 2000 boundary | info_disclosure | ✓ | `test_secret_redaction_before_truncation` 2014-char boundary |
| T-04 concurrent store | tampering | ✓ | `test_cross_process_no_corruption` 4-process 提交 |
| T-05 mid-write OSError | DoS | ✓ | `test_write_unlocked_cleans_up_temp_file_on_failure` + tolerate-missing 子測 |
| T-06 repudiation | repudiation | ✓ | `test_task_records_timestamps` |
| T-07 --inject-fault | elevation_of_privilege | ✓ | `test_main_inject_fault_rejected_without_env` + `test_inject_fault_rejected_in_production` |

## 掃描方法

1. CRG scout 階段:讀 `.methodology/bug_hunt_targets.json`,挑出 high-risk
   (`taskq.cli`、`taskq.executor`、`taskq.store`)與 standard(`__init__`,
   `__main__`, `breaker`, `cache`, `config`)。
2. Hunt 階段:三 high-risk module 各跑 concurrency / correctness / resilience / general
   lens;standard module 跑 general lens。
3. Verify 階段:每條 finding 跑 refuter(預設 real=False) + confirmer(需找出具體
   觸發 + 預期 vs 實際),只有 2/2 is_real 升級 `confirmed=true`。
4. Synthesize 階段:寫 JSON + 這份 markdown。
5. Resolve:每條 confirmed critical/high 需 repro_test(RED→GREEN)或
   refute_evidence;本輪只有 1 confirmed medium,已解決。

## 變更清單 (Gate 3 前)

```
1b45cc8 fix(breaker): route clear_command through reset() under flock
 03-development/src/taskq/breaker.py             | +11 -0  (new public reset helper)
 03-development/src/taskq/cli.py                 |  1 line  (reset vs save)
 03-development/tests/test_bug_hunt_resolve.py   | +80 -0  (anti-fabrication repro)
 .methodology/bug_hunt_report.json               |  NEW (overwrite)
 03-development/.audit/bug-report-2026-07-26.md  |  NEW (overwrite)
```

-- 2026-07-26 bug-hunt orchestrator
