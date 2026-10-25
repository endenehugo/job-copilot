# Locust 阶段对比

> 汇总指标来自各阶段的 Locust `_stats.csv`。P50/P90/P95/P99 只读取 Locust 导出的对应字段；不可用时显示 N/A。平均响应时间不能替代分位数。

## 阶段总览

| 阶段 | 请求数 | 成功数 | 失败数 | 失败率 | Avg | P50 | P90 | P95 | P99 | RPS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| stage-01 | 155 | 155 | 0 | 0.00% | 80.7 ms | 79.0 ms  | 100.0 ms  | 110.0 ms  | 150.0 ms | 2.61 |
| stage-05 | 573 | 573 | 0 | 0.00% | 76.9 ms | 75.0 ms  | 96.0 ms  | 100.0 ms  | 130.0 ms | 9.65 |
| stage-10 | 1,157 | 1,157 | 0 | 0.00% | 76.9 ms | 75.0 ms  | 95.0 ms  | 100.0 ms  | 130.0 ms | 19.46 |
| stage-20 | 3,573 | 3,573 | 0 | 0.00% | 79.7 ms | 76.0 ms  | 100.0 ms  | 110.0 ms  | 130.0 ms | 29.76 |
| stage-30 | 3,585 | 3,585 | 0 | 0.00% | 78.2 ms | 75.0 ms  | 100.0 ms  | 110.0 ms  | 150.0 ms | 29.67 |
| stage-40 | 3,604 | 3,604 | 0 | 0.00% | 77.4 ms | 74.0 ms  | 98.0 ms  | 110.0 ms  | 180.0 ms | 29.58 |

## 相邻阶段变化

| 对比 | Avg 变化 | P95 变化 | RPS 变化 |
|---|---:|---:|---:|
| stage-01 → stage-05 | -4.7% | -9.1% | +269.6% |
| stage-05 → stage-10 | +0.0% | +0.0% | +101.7% |
| stage-10 → stage-20 | +3.7% | +10.0% | +52.9% |
| stage-20 → stage-30 | -1.9% | +0.0% | -0.3% |
| stage-30 → stage-40 | -1.1% | +0.0% | -0.3% |

## 接口明细

| 阶段 | 接口/请求名 | 请求数 | 失败数 | 失败率 | Avg | P50 | P90 | P95 | P99 | RPS |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| stage-01 | GET /conversation/detail | 54 | 0 | 0.00% | 78.9 ms | 79.0 ms  | 100.0 ms  | 100.0 ms  | 150.0 ms | 0.91 |
| stage-01 | GET /conversation/list | 78 | 0 | 0.00% | 81.5 ms | 80.0 ms  | 100.0 ms  | 110.0 ms  | 130.0 ms | 1.31 |
| stage-01 | GET /skills | 9 | 0 | 0.00% | 74.7 ms | 73.0 ms  | 99.0 ms  | 99.0 ms  | 99.0 ms | 0.15 |
| stage-01 | GET /system/health | 13 | 0 | 0.00% | 70.8 ms | 78.0 ms  | 81.0 ms  | 85.0 ms  | 85.0 ms | 0.22 |
| stage-01 | [setup] POST /conversation/create | 1 | 0 | 0.00% | 307.2 ms | 310.0 ms  | 310.0 ms  | 310.0 ms  | 310.0 ms | 0.02 |
| stage-05 | GET /conversation/detail | 202 | 0 | 0.00% | 75.0 ms | 75.0 ms  | 95.0 ms  | 99.0 ms  | 110.0 ms | 3.40 |
| stage-05 | GET /conversation/list | 269 | 0 | 0.00% | 77.7 ms | 76.0 ms  | 97.0 ms  | 100.0 ms  | 110.0 ms | 4.53 |
| stage-05 | GET /skills | 52 | 0 | 0.00% | 71.7 ms | 72.0 ms  | 91.0 ms  | 110.0 ms  | 130.0 ms | 0.88 |
| stage-05 | GET /system/health | 45 | 0 | 0.00% | 69.4 ms | 67.0 ms  | 87.0 ms  | 89.0 ms  | 93.0 ms | 0.76 |
| stage-05 | [setup] POST /conversation/create | 5 | 0 | 0.00% | 230.5 ms | 220.0 ms  | 330.0 ms  | 330.0 ms  | 330.0 ms | 0.08 |
| stage-10 | GET /conversation/detail | 384 | 0 | 0.00% | 73.8 ms | 72.0 ms  | 92.0 ms  | 99.0 ms  | 110.0 ms | 6.46 |
| stage-10 | GET /conversation/list | 542 | 0 | 0.00% | 79.6 ms | 79.0 ms  | 97.0 ms  | 100.0 ms  | 120.0 ms | 9.12 |
| stage-10 | GET /skills | 108 | 0 | 0.00% | 69.3 ms | 69.0 ms  | 83.0 ms  | 89.0 ms  | 93.0 ms | 1.82 |
| stage-10 | GET /system/health | 113 | 0 | 0.00% | 69.9 ms | 71.0 ms  | 87.0 ms  | 93.0 ms  | 110.0 ms | 1.90 |
| stage-10 | [setup] POST /conversation/create | 10 | 0 | 0.00% | 214.1 ms | 210.0 ms  | 280.0 ms  | 280.0 ms  | 280.0 ms | 0.17 |
| stage-20 | GET /conversation/detail | 1,300 | 0 | 0.00% | 78.3 ms | 75.0 ms  | 100.0 ms  | 110.0 ms  | 120.0 ms | 10.83 |
| stage-20 | GET /conversation/list | 1,609 | 0 | 0.00% | 84.0 ms | 80.0 ms  | 110.0 ms  | 110.0 ms  | 130.0 ms | 13.40 |
| stage-20 | GET /skills | 325 | 0 | 0.00% | 68.7 ms | 66.0 ms  | 90.0 ms  | 98.0 ms  | 100.0 ms | 2.71 |
| stage-20 | GET /system/health | 315 | 0 | 0.00% | 66.9 ms | 65.0 ms  | 87.0 ms  | 92.0 ms  | 100.0 ms | 2.62 |
| stage-20 | [cleanup] POST /conversation/delete | 4 | 0 | 0.00% | 87.9 ms | 86.0 ms  | 110.0 ms  | 110.0 ms  | 110.0 ms | 0.03 |
| stage-20 | [setup] POST /conversation/create | 20 | 0 | 0.00% | 211.3 ms | 210.0 ms  | 290.0 ms  | 300.0 ms  | 300.0 ms | 0.17 |
| stage-30 | GET /conversation/detail | 1,233 | 0 | 0.00% | 76.3 ms | 74.0 ms  | 97.0 ms  | 100.0 ms  | 110.0 ms | 10.20 |
| stage-30 | GET /conversation/list | 1,584 | 0 | 0.00% | 81.8 ms | 78.0 ms  | 100.0 ms  | 110.0 ms  | 120.0 ms | 13.11 |
| stage-30 | GET /skills | 372 | 0 | 0.00% | 69.2 ms | 68.0 ms  | 88.0 ms  | 94.0 ms  | 100.0 ms | 3.08 |
| stage-30 | GET /system/health | 346 | 0 | 0.00% | 67.3 ms | 65.0 ms  | 88.0 ms  | 92.0 ms  | 98.0 ms | 2.86 |
| stage-30 | [cleanup] POST /conversation/delete | 20 | 0 | 0.00% | 95.6 ms | 94.0 ms  | 120.0 ms  | 130.0 ms  | 130.0 ms | 0.17 |
| stage-30 | [setup] POST /conversation/create | 30 | 0 | 0.00% | 197.3 ms | 200.0 ms  | 250.0 ms  | 250.0 ms  | 270.0 ms | 0.25 |
| stage-40 | GET /conversation/detail | 1,323 | 0 | 0.00% | 75.4 ms | 73.0 ms  | 95.0 ms  | 100.0 ms  | 110.0 ms | 10.86 |
| stage-40 | GET /conversation/list | 1,556 | 0 | 0.00% | 79.7 ms | 77.0 ms  | 100.0 ms  | 110.0 ms  | 130.0 ms | 12.77 |
| stage-40 | GET /skills | 313 | 0 | 0.00% | 67.9 ms | 66.0 ms  | 86.0 ms  | 94.0 ms  | 100.0 ms | 2.57 |
| stage-40 | GET /system/health | 337 | 0 | 0.00% | 65.0 ms | 64.0 ms  | 83.0 ms  | 90.0 ms  | 100.0 ms | 2.77 |
| stage-40 | [cleanup] POST /conversation/delete | 35 | 0 | 0.00% | 119.4 ms | 95.0 ms  | 210.0 ms  | 300.0 ms  | 330.0 ms | 0.29 |
| stage-40 | [setup] POST /conversation/create | 40 | 0 | 0.00% | 195.6 ms | 200.0 ms  | 230.0 ms  | 250.0 ms  | 260.0 ms | 0.33 |

## 口径与限制

- 阶段总览按 Locust `Aggregated` 行报告，仅包含统计 CSV 中的请求；初始化请求以 `[setup]` 单列。Locust 2.47 的 CSV 快照可能早于 `on_stop` 清理请求，因此 `[cleanup]` 可能只出现在进程结束时的控制台汇总中，需一并核对。
- 若输入文件没有聚合行，脚本只对计数、失败率、加权平均值和可加总 RPS 做可计算的汇总；总体分位数显示 N/A，因为不能由各接口分位数合成。
- RPS 直接读取 CSV。请求数为 0 或字段缺失时不推算、不用 0 伪装。
- 阶段标签默认取输入目录名；请使用包含并发数/场景的目录名以便留档。
- 小样本提示（少于 100 个请求）：Locust 的分位值仍按原样展示，但 P95/P99 稳定性不足，谨慎解读：stage-01 GET /conversation/detail (n=54)；stage-01 GET /conversation/list (n=78)；stage-01 GET /skills (n=9)；stage-01 GET /system/health (n=13)；stage-01 [setup] POST /conversation/create (n=1)；stage-05 GET /skills (n=52)；stage-05 GET /system/health (n=45)；stage-05 [setup] POST /conversation/create (n=5)；stage-10 [setup] POST /conversation/create (n=10)；stage-20 [cleanup] POST /conversation/delete (n=4)；stage-20 [setup] POST /conversation/create (n=20)；stage-30 [cleanup] POST /conversation/delete (n=20)；stage-30 [setup] POST /conversation/create (n=30)；stage-40 [cleanup] POST /conversation/delete (n=35)；stage-40 [setup] POST /conversation/create (n=40)
