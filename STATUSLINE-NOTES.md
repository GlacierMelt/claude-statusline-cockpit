# 实现与维护核对笔记

本文是当前代码的维护索引，配合 README 阅读；不是本机部署日志，也不是旧修复报告的逐段追加。历史本机核验留在被忽略的 `verification/`，其中早期白名单、空闲补桶、问号/黑点等描述已被后续实现取代。

## 1. 审核基准与不可擅改项

- 原项目基准 `0983e03` 与未修改的 06:26 原项目备份确认 `hit` 真彩 `#2C687B`、256 色 248。旧安装/review 的 `#233D4D` 不是该标签基准。
- 保持数字 role 红色序列、小数点/百分号独立颜色、256 色粗体平色、一位小数及真正 `100.0%`。
- 保持八档字形/颜色、12 格、原最低档未知占位、宽窄换行与所有字体/布局，不以“改进”名义新增 `~`、问号柱或黑点。
- 本轮仅 README、本文与 `.gitignore`；不改运行语义/测试/demos，不安装、不动 settings/pin、生产账本，不提交/推送。

## 2. 从入口到输出：代码核对点

| 入口/函数 | 应说明的行为 |
| --- | --- |
| shell Python 选择及 helper fallback | override/pin/`python3`；helper 失败时独立 parser。所有 Python 不可用不能保证保留 payload |
| `statusline_input.context_fields` | current R+W+U → total_input_tokens；官方百分比优先；未知不造零；output 不加入 |
| `input_fields` | model/effort/cost/path 独立于 SQLite；控制字符清理 |
| `discover` | 所有项目 main/subagent、可选现存 transcript_path、可选 feed；scope 不限制摄取 |
| `parse_transcript` | 版本元数据，逐项身份/带时区时间/assistant角色/stop/整型 R-W-U 合同 |
| `transcript_exclusion_reason` / `quarantine` | 显式 API-error/synthetic 隔离；不以全零或 stop_sequence 判定请求；保留原 events |
| `History.put` / `_insert` | 五段唯一身份、final usage 冲突诊断、未来隔离、pending累计替换、完成后入账 |
| `_counter` | 显式 epoch 基线、首快照不加权、不能按诊断 miss/重绘次数造用量 |
| `ingest_file` | 完整行 checkpoint；旋转/截断安全重读；坏完整 JSON 回滚 |
| `resolve_transcript_issues` | 原诊断保留；路径/行/时间/scope/已入账最终计数匹配后才恢复 |
| `view` / `_build_view` | event时间轴、最新12个活跃桶、scope、padding/quality；非墙钟小时 |
| `percentage` / `ramp_step` | 先ΣR/W/U再除、十分之一整数舍入；八档用未舍入单桶值 |
| `bridge` / `save_fallback` | DB COMMIT后使用candidate，sidecar同scope/锁/拒绝旧revision/原子替换 |
| `install_support.install` | runtime/settings预验证；完整包staging；包与settings各自replace，非跨文件全局原子 |

## 3. 第二行的现行不变量

```text
无新有效用量 → 整行不变
同桶新的唯一完成用量 → 最右格/整体重算，不平移
下一更晚有请求桶 → 只推进一格，跳过任意数量空闲桶
```

- 十二格是 12 个有请求证据的五分钟桶；不是连续钟表一小时。
- 每桶用 `event_ms // 300000` 定位。padding 无时间/权重，未知诊断不能制造活动槽位。
- 默认跨项目/会话/子会话共享；显式 scope 是 AND 精确过滤，sidecar 也按同 scope 回退。
- `R/(R+W+U)` 不取请求百分比平均，不用 request/miss 次数；TTL 不重分类 R，不推进轴。
- 真零完成请求有身份/活跃桶但权重加零；无分母内部未知。客户端全零错误提示不占桶。
- 未完成流只保留 pending，累计字段覆盖而非相加；重复 stop 与重复读取不加权。
- 迟到事件、历史恢复、明确隔离、scope切换与投影策略升级可能修正视图，不等于空闲滚动。
- `revision` 是投影修订号，既可能因新事件增加，也可能因已证实的隔离/诊断恢复增加，不能当请求计数。
- UI 可以不显露 `partial/unknown`；必须在私有审计中保留质量，不把占位声称为实测0%。

## 4. 合同兼容与恢复的维护规则

应用版本不再作为 allowlist gate。保持已有合同的更新/新增字段可以直接读；改名、类型/语义变化或新 stop reason 必须显式验证，不能猜测。观察版本清单不决定资格。

`TRANSCRIPT_CONTRACT_POLICY` 只在接受合同确实变化时提升。每路径 `transcript_replays` 记录该号；首次或升级从头重放完整行，已有完整身份键去重，之后继续增量。

`resolved_issues` 仅消解已证实恢复的合同诊断：匹配来源路径、原行号、原事件时间、project/session/conversation，确认该完整请求的最终R/W/U已经入账且未隔离。保留原 `issues`，冲突/未来等非可恢复诊断不自动解除。只恢复诊断也要失效投影。

重放、入账、恢复、checkpoint、策略号与视图共用 refresh 事务；完整 JSON 坏行使其全部回滚，半写尾行保留在最后一个完整换行位置。不得先提升 replay 标记后再尝试补账。

2026-10-03 上轮本机报告补回5个唯一请求、保留9条原诊断并标记恢复；83.0%是当时窗口快照。三套Python的106项通过也是该轮记录，不是固定输出或任意未来环境承诺。

## 5. 状态文件与故障边界

| 状态 | 保留理由 |
| --- | --- |
| events | 五段去重键、事件/观察时间、R/W/U、来源版本/schema、粗区间质量 |
| checkpoints | 源路径、device/inode、offset/sequence、边缘hash，提交与用量一致 |
| pending | 尚未完成的累计字段，不应进入活跃轴 |
| counters | source/project/session/conversation/epoch 基线，首快照不差分到零 |
| issues / resolved_issues | 原始诊断及已证实恢复证据，不能静默删除原诊断 |
| quarantined | 明确客户端错误/未来事件等隔离身份，投影不得重新计入 |
| transcript_replays | 各路径合同重放策略，不是Claude版本号 |
| meta / views | 全局投影revision、一次性隔离策略、各scope已提交视图 |
| `.view.json` sidecar | 提交后可读的fallback，不含正文但仍含私有时间/计数/scope |

SQLite WAL/FULL/BEGIN IMMEDIATE保护refresh；初始化与sidecar不在同一数据库事务里。sidecar写失败不撤销已提交candidate，也意味着下一次fallback可能较旧。scope匹配/轴策略校验失败不能借用其他保存视图。

维护只compact，不TTL删除、不tail-N裁剪。旧三列日志不导入、不改写。默认账本会增长；视图重建扫描保留的所选events，不承诺常数成本。

**生产只读核验禁用 `History`、bridge、`--inspect`、`--maintain`。** `--inspect`也会建schema/WAL并写视图。需要读生产库只能使用SQLite URI `mode=ro`；维护/回退演练用隔离副本。旧DB快照不能覆盖实时账本。

## 6. 测试和发布核对清单

- [ ] `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`；记录实际解释器/项数/结果，不沿用旧日志冒充本轮。
- [ ] `bash -n statusline-command.sh install.sh`、可用时 `node --check demos/cache-demo.js`、`git diff --check`。
- [ ] fixture证据覆盖同桶更新、跨一天只移一格、空闲第二行byte freeze、weighted oracle、完整身份、零/流/客户端错误、scope、失败回滚与安全恢复。
- [ ] 宽窄和truecolor/256色各测；fixture/浏览器通过不等于真实hostfooter无裁剪。Linux和任意未来Claude版本不冒充已现场验证。
- [ ] 安装只在明确授权时进行，验证整包/pin/settings一致；不为刷新UI发`hi`或API请求。
- [ ] 检查stage没有转录/DB/备份/完整配置/本机verification产物。`.gitignore`仅保护未跟踪候选，不替代内容审查，不改变已跟踪项。
- [ ] 保留已有未提交/未跟踪成果；不reset、不清历史、不自动commit/push；需要远端同步先向用户列出待同步内容。

可发布的是运行源码、安装器、通用文档、合成测试与合成演示数据。专项 `verify_*` 脚本应先读其行为再单独授权；输出留私有，不进入公开库。

## 7. 本轮发现但不修改的代码/演示表述

- demos legend仍有`? unknown`、`~ partial evidence`文字，但实际JS状态行复用原最低档并去掉`~`。这里仅标记文案不一致，不改演示UI。
- shell旧注释写“right-aligned cost”，实际输出为固定一格间距紧跟前段；README依据输出代码，不照抄注释。
- 第二行默认host margin源于特定版本测量；第一行不是完整Unicode宽度计算，第三行未按COLUMNS主动截断。不要把三逻辑行承诺成任意终端的三物理行。
- helper独立回退不等于任意解释器故障都可恢复；解释器可定位但坏掉、独立parser也失败时，payload元数据无法保留。

任何后续修订这些点都应另行确认范围，并沿用现有UI/配色基准，而非在文档整理中顺手改运行行为。
