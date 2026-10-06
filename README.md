# Claude Statusline Cockpit

一个供 Claude Code 调用的三行终端状态栏：第一行显示当前输入上下文与会话费用，第二行显示真实完成请求的缓存读取命中率、最近 12 个活跃五分钟桶及最右桶缓存写入累计量，第三行显示目录与 Git 状态。运行时使用 Bash、Python 标准库和 SQLite，不依赖 `jq`，不发送 API 请求。

> 本文描述当前仓库代码，不把历史修复报告当作现行行为。界面保持原项目视觉约定；缓存统计是完成请求的输入 token 比例，不是“状态栏刷新次数命中率”，也不是缓存 TTL 倒计时。

## 快速安装

需要 Python 3.9+、Bash、`awk` 和 `git`，macOS 默认自带的就够，不需要 `jq` 或任何第三方 Python 包。

```sh
git clone https://github.com/GlacierMelt/claude-statusline-cockpit.git
cd claude-statusline-cockpit
bash install.sh
```

首次安装或改变 settings 中的命令配置后，**新开一个 Claude Code 会话**以加载配置。如果状态栏是空的，先接受该目录的 workspace trust 对话框。已有安装只更新同一路径下的脚本/运行包、settings 命令不变时，下次状态栏调用就会读取新内容，不要求为本次 UI 调整重启会话或发送新消息。

安装器把运行包复制到 `~/.claude/statusline-cockpit/`，只改 `~/.claude/settings.json` 里的 `statusLine`，其他设置不动。改之前会给旧运行包和 settings 各留一份带时间戳的备份。

- **装到别的配置目录**：`CLAUDE_CONFIG_DIR=~/my-claude bash install.sh`
- **指定 Python**（比如 PATH 里的 `python3` 是 Conda 环境）：`CACHE_HISTORY_PYTHON=/usr/bin/python3 bash install.sh`
- **升级**：在仓库目录里 `git pull && bash install.sh`
- **卸载**：在 Claude Code 里运行 `/statusline delete`，或者手动删掉 settings 里的 `statusLine` 键，再删除 `~/.claude/statusline-cockpit/`。缓存账本在 `~/.local/state/claude-statusline-cockpit/`，不需要的话可以一起删掉。

安装原理、回退方法和全部配置项见后文「安装、升级与安全回退」和「配置项」。

## 目录

- 快速安装
- 三行分别表示什么
- 架构与运行数据流
- 逐项功能实现原理
- 安装、升级与安全回退
- 配置项
- 测试与演示
- 限制、故障排查与隐私边界

## 三行分别表示什么

下面是说明布局的示意，不是固定统计值；实际颜色由 ANSI 序列产生：

```text
Opus 5.5 | high  tok 94.4k/200k (47.2%)  ■■■■■■······ $0.42
▲ hit 95.0%  ▁▂▃▄▅▆▇█████   💭 555k
~/work/example · main*
```

| 位置 | 数据与含义 | 不代表什么 |
| --- | --- | --- |
| 第一行 | stdin payload 中的模型、effort、输入侧上下文、会话费用 | 不能把 output tokens 加到上下文分子；费用不由本项目按 token 定价推算 |
| 第二行数字 | 所选范围内最近 12 个活跃桶的 `ΣR / (ΣR + ΣW + ΣU)` | 不是各请求百分比的平均值，也不是最右格的单桶命中率 |
| 第二行 12 格 | 从旧到新的活跃桶，每格高度取该桶自身 token 比例 | 不是连续一个钟表小时；不按空闲时长补格 |
| 第二行 `💭` 角标 | 当前 scope 中最右活跃五分钟桶的唯一有效请求缓存写入量 `ΣW`（原始 `cache_creation_input_tokens`） | 不是平均值、每秒速率或 12 格窗口写入量之和 |
| 第三行 | 当前工作目录、Git 分支和 `*` dirty 标记 | 不是仅当前项目的缓存统计；默认第二行跨项目/会话共享 |

**第二行的正常刷新规则：**

1. 没有新有效完成用量时，整行冻结；重绘、重启、等待一天或 TTL 到期都不会滚动。
2. 同一五分钟桶内，每个唯一完成请求入账后重算最右格和整体百分比，其他格不平移。若单桶比率没有跨过八档边界，最右格可能视觉不变。
3. 下一个有有效请求的更晚桶，只推进一格；中间空闲五分钟、五小时或一天都不补移多个格。一次补回多个历史活跃桶时，按实际有请求的桶重建，而不是按经过时间补位。
4. 去重重放、未完成流、客户端 API-error/synthetic 提示不是新有效用量，不推动活跃轴。合法零用量请求有完成证据，仍占活跃桶，但给 token 总和加零。

上述冻结是**第二行**的规则。第一行的模型/上下文/费用和第三行的目录/Git 仍可随 payload 与工作树变化。显式切换 scope 或已证实的账本纠错/投影策略升级可以重建第二行，不是空闲时间驱动的滚动。

### 缓存写入角标外观

**当前最终样式**：幅度条与 `💭` 之间恰好保留 **3 个普通空格（U+0020）**，不显示 `|` 分隔符；emoji 后再留 **1 个空格**。原有百分比与幅度条之间的 2 个空格不变。

移除的仅是第二行写入角标前的竖线；第一行模型与 effort 之间的 `|` 保持原样，数字/小数点和 `k`/`M` 仍按下表着色并加粗。

| 部分 | 真彩 | 256 色兼容索引 | 加粗 |
| --- | --- | --- | --- |
| 数字、小数点 | `#2388A8` | 31 | 是 |
| 单位 `k` | `#F7D64F` | 221 | 是 |
| 单位 `M` | `#E6AD35` | 178 | 是 |
| `💭` 与空格 | 保留宿主 emoji 外观，不单独套颜色 | 不适用 | 否 |

可确认的桶内写入为零时显示 `💭 0`；最右桶质量未知或无有效桶时隐藏角标。宽度不足时隐藏整个角标，不留尾随空格，不缩减原有百分比、12 格或其他行。数字沿用第一行 `humanise()` 的 k/M 舍入：例如 `1234 → 1.2k`、`555000 → 555k`、`999999 → 1000k`、`1200000 → 1.2M`；账本始终保留精确整数。

## 架构与运行数据流

```text
Claude Code stdin JSON
  └─ statusline-command.sh（读取 payload、选择 Python）
       ├─ lib/cache_history.py / bridge
       │    ├─ lib/statusline_input.py：独立提取第一、三行元数据
       │    ├─ discover：主会话 + subagent JSONL + 可选集成 feed
       │    ├─ lib/cache_sources.py：逐记录合同校验
       │    ├─ SQLite 事务：隔离/重放/用量/pending/checkpoint/投影
       │    └─ COMMIT 后发布同 scope 的 .view.json sidecar
       ├─ helper 失败：lib/statusline_input.py 独立读取 payload + 已提交 sidecar
       └─ Bash：ANSI 配色、宽度降级、12 格渲染、Git → stdout
```

| 文件 | 职责 |
| --- | --- |
| `statusline-command.sh` | 渲染与 Python 选择；不从刷新频率或墙钟时间制造请求 |
| `lib/statusline_input.py` | 真实 JSON 解析、输入侧上下文、模型/effort/费用/目录、只读 sidecar 回退；不依赖 SQLite 或 source adapter |
| `lib/cache_sources.py` | 真实 transcript 与显式 `cockpit-events/v1` 的校验，产出 `Record`、`Issue`、`Excluded` |
| `lib/cache_history.py` | 去重账本、流合并、counter 基线、增量追读、合同重放、质量与活跃桶投影 |
| `install.sh` / `lib/install_support.py` | 显式安装入口、环境探测、包 staging、激活、settings 合并与失败恢复 |
| `tests/` | 隔离 fixture 测试、合同/原 UI 回归、演示向量生成；本地源/host 核验脚本不是普通单测 |
| `demos/` | 从 SQLite fixture 导出的浏览器演示，不读取生产遥测 |

## 逐项功能实现原理

### 1. 模型与 effort

- `model.display_name` 作为徽章文字；去掉末尾括号限定词（例如 `(1M context)`），空值显示 `Claude Code`。
- `effort.level` 直接显示，不根据模型名猜档位；缺失时省略。所有 effort 档位使用同一琥珀色。
- 文本先把控制字符变为空格，避免将 payload 中的转义字符当成终端命令。宽度收缩时按现有布局依次去进度条、费用、徽章留白、effort、token 段，最后截短模型文字；不重新设计样式。

### 2. 仅输入侧的上下文与百分比

`context_fields()` 的优先顺序：

1. 若 `context_window.current_usage` 包含三个合法整数字段，`used = cache_read_input_tokens + cache_creation_input_tokens + input_tokens`。
2. 否则使用合法的 `context_window.total_input_tokens`。
3. 两者均不可用，`used` 保留未知；**不加** `output_tokens` 或 `total_output_tokens`，也不以缺失冒充零。

容量使用正整数 `context_window_size`。百分比优先用有限、非负的 `used_percentage`，没有时才计算 `100 × used / size`。文本百分比按一位小数格式化并去掉尾部 `.0`；进度条单独四舍五入到整数、限制在 0–100，不改原始文本值。`humanise()` 把计数变成 `94.4k`、`200k`、`1.2M`；缺失值是 `?`。

这两个来源可能给出不同的文本用量与官方百分比，本项目遵守优先级，不强行让百分比反推 token。这个当前上下文快照只负责第一行，**从不作为完成请求入账**。

### 3. 会话费用与独立元数据保留

`cost.total_cost_usd` 经数字校验后显示为美元两位小数，合法零显示 `$0.00`，缺失或非法则省略。真彩使用逐字符浅灰至浅青色渐变（每个字符一种颜色），256 色分支使用平色；费用紧跟进度条/前段，不悬浮到终端右边。

Python 选择为 `CACHE_HISTORY_PYTHON` → 包内 `.python-path` → `python3`；指定解释器未能通过 `command -v` 时回到 `python3`。账本 helper 缺失、导入崩溃或执行失败时，Bash 仍尝试独立的 `statusline_input.py`，保留模型、上下文、费用、目录及同 scope 的已提交历史。

**边界：**独立解析器也需要能运行的 Python。若解释器路径存在但不能执行 helper，并不保证自动切换到另一个 Python；若所有 Python 均不可用或独立解析器本身损坏，只能输出默认占位，不能保证保留 payload 元数据。缺失 pin 的回退与账本 helper 失败的回退是两条不同路径。

### 4. 真实完成请求的采集与版本兼容

默认扫描 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects`：

- `*/*.jsonl`：主会话，project 身份来自项目目录名，conversation 为 `main`。
- `*/*/subagents/*.jsonl`：子代理，conversation 为 `subagent:<文件stem>`。
- payload 的 `transcript_path` 若存在也会纳入；已扫描路径不重复添加。路径尚未创建时不阻断其他会话；根外路径的 project 使用父目录路径哈希标识。

当前 adapter **不按应用版本白名单决定入账**。`INSPECTED_TRANSCRIPT_VERSIONS` 只是观察清单，版本号写入来源元数据；缺失/`null` 记为 `unreported`，非法版本类型或控制字符仍拒绝。每条 assistant 记录必须逐项满足：

- `type == "assistant"` 且 `message.role == "assistant"`；非 assistant 行忽略。
- project、session、conversation、`message.id` 是非空合法标识。`sessionId` 或 `session_id` 可用，同时存在且不一致则拒绝。
- `timestamp` 带时区，可解析且不早于 epoch；未来事件隔离，不因稍后墙钟追上而自动放行。桶时间取事件时间，不取重绘时间。
- `stop_reason` 必须存在。`null` 保存为未完成 pending；完成值仅接受 `end_turn`、`tool_use`、`stop_sequence`、`max_tokens`、`pause_turn`、`refusal`、`model_context_window_exceeded`。
- 完成 usage 的 `cache_read_input_tokens`（R）、`cache_creation_input_tokens`（W）、`input_tokens`（U）全部存在，类型为非负整数，最大 `2**53 - 1`。布尔、浮点、数字字符串、负数均不合法。

因此，只要保持已校验的字段结构与语义，新版本号和新增字段可直接工作；必需字段改名、计数类型变化、未知完成值或语义改变不能靠猜测接受。这是**合同兼容**，不是无条件适配任意未来格式。

### 5. 完整身份去重、stream pending 与客户端错误隔离

事件唯一键是：

```text
(source, project_id, session_id, conversation_scope, event_id)
```

真实 transcript 的 `source` 为 `claude-transcript`，`event_id` 为 `message.id`。同一请求多个内容块、反复读同一文件、旋转后重读、恢复重放均以完整键去重；不同项目/会话/子会话即使同秒发生也不互相覆盖。已入账身份的另一条 `request` 若 R/W/U 不同，记 `conflicting_final_usage`，不悄悄改账。

未完成记录只进 `pending`，usage 字段按累计快照**替换/合并**，不是逐 chunk 相加；sequence 不前进的流记录忽略。完成 transcript 使用最终完整 usage；可选 feed 的 `stream_stop` 使用合并后的完整 R/W/U。入账后删除对应 pending。流中断或没有可靠完成证据，就不占活跃桶。

`isApiErrorMessage == true` 或 `message.model == "<synthetic>"` 明确表示客户端提示，优先排除；即使带 `stop_sequence` 与三项零计数也不是完成请求。有可信身份时写 `quarantined`，投影排除该键；无可靠身份的明确错误提示也不当成“缺失用量请求”。旧误计行通过一次性源证据修复隔离，原始 events 不删除。

合法完成记录的 `R=W=U=0` 则不同：保留该请求身份和活跃桶，不增加 token 权重，分母为零时该格无可测命中率。`R=0` 但 `W+U>0` 才是真实 0% 命中。

### 6. 默认共享范围与显式 scope

默认 `scope={}`，第二行合并所有已发现项目、会话、main/subagent 的有效账本事件，而不是仅当前目录或当前 payload 会话。

`CACHE_SCOPE_PROJECT`、`CACHE_SCOPE_SESSION`、`CACHE_SCOPE_CONVERSATION` 可独立使用，非空条件以 AND 精确过滤。project 应填写 adapter 使用的目录身份（不是随手填写 cwd），conversation 为 `main` 或对应 `subagent:<stem>`。投影与 sidecar 均按规范化 scope JSON 分开保存，故障回退也不会把共享视图借给显式 session。

scope **只过滤投影，不限制源扫描/入账**。切换时其他范围的历史仍保留；缓存 TTL 不参与过滤。

### 7. 先累加 token，再计算命中率

完成请求的输入缓存读取比例定义为：

```text
R = cache_read_input_tokens
W = cache_creation_input_tokens
U = input_tokens
单桶 hit = 100 × Σ桶R / (Σ桶R + Σ桶W + Σ桶U)
整体 hit = 100 × Σ选中桶R / (Σ选中桶R + Σ选中桶W + Σ选中桶U)
```

只计算输入 token：不包含 output，不用请求条数，也不用 prompt-cache miss 诊断计数替代 W/U。缓存创建 W 是分母的一部分，不是命中分子。

例：请求 A `(R,W,U)=(90,0,10)` 为 90%；请求 B `(0,900,100)` 为 0%。合并结果是 `90/(90+900+110)=8.1818…%`，显示 **8.2%**，不是 `(90%+0%)/2=45%`。

`percentage()` 使用整数算术将结果四舍五入到十分之一，Bash 补齐 `.0`。真实 100% 显示 **100.0%**，不限制为 99.9%。分母为零内部是 `?%`，不是实测 0%。

### 8. 80–100% 八档映射与原 UI

高度取每桶未舍入比率 `p`，而非已格式化的整体百分比：

```text
step = clamp(floor((p - 80) / 2.5), 0, 7)
```

| code | 单桶比率范围 | 字形 | 真彩 | 256 色索引 |
| --- | --- | --- | --- | --- |
| 0 | 0%–不足 82.5%（≤80% 均压到最低档） | ▁ | `#F5C9B5` | 223 |
| 1 | 82.5%–不足 85% | ▂ | `#E1CDBE` | 223 |
| 2 | 85%–不足 87.5% | ▃ | `#CAD2C8` | 187 |
| 3 | 87.5%–不足 90% | ▄ | `#B2D5D1` | 151 |
| 4 | 90%–不足 92.5% | ▅ | `#ACDAD8` | 115 |
| 5 | 92.5%–不足 95% | ▆ | `#BBE1DF` | 115 |
| 6 | 95%–不足 97.5% | ▇ | `#C9E7E6` | 152 |
| 7 | 97.5%–100% | █ | `#D8EEED` | 152 |

整体 83.0% 可以与最右格 `▁` 同时出现，因为整体是窗口总和，最右格只看最新桶。八档把 80–100% 放大，低于 80% 的不同值都显示最低档，不能从 `▁` 反推出准确比率。

未知、未使用左侧槽位、零分母在 UI 都沿用原最低档占位，不新增 `~`、问号柱或黑点。无可测整体值显示 `0.0%` 占位；内部 `quality`、`?`、`-`、`?%`/`~…%` 仍用于诊断，**占位不意味着确认 0%**。

`▲` 三角真彩 **`#EBDF33`**，256 色 **220**。`hit` 标签基准为原项目 `0983e03` 与未修改的 06:26 原项目备份：真彩 **`#2C687B`**，256 色 **248**，不能用旧安装/review 的 `#233D4D` 代替。真彩数字依次用 `#FF0000`、`#FF1B1D`、`#FF393A`（第四位及以后继续第三色）；小数点 `#C0C5C9`、百分号 `#BBD5DA` 独立着色且不推进数字索引。256 色数字/标点按现有分支统一用粗体 196，不伪造真彩逐位近似。

### 9. 12 个活跃五分钟桶与冻结

`BUCKET_MS=300000`，桶号为 `event_ms // BUCKET_MS`；固定边界而非“从首个请求起滚动五分钟”。`view()` 取有效 events 的桶号，去重排序后选择最新 12 个。每桶先累加 R/W/U；不足 12 个时左补 `padding`，没有虚构时间戳或 token 权重。

例如 10:01、10:03 在同桶合并；随后 14:12 产生一个新活跃桶，即使隔了四小时也只前进一格。12 桶因此可能跨越数小时乃至多天。缓存 TTL 不决定“是不是命中”或“是否移动”：完成 usage 报告 R，就按 R 计入，不按年龄扣成 miss。

视图锚点是最新有效事件时间，`revision` 变化才通常重算；空闲重绘返回同一已保存视图。`active-five-minute/v1` 标记投影轴策略：旧轴视图只重建一次，账本不清空，独立回退拒绝把旧策略 sidecar 当作当前视图。迟到/恢复历史数据可能修正选中桶；这不是 TTL 滚动。

### 9.1. 最右活跃桶缓存写入角标

角标与百分比、幅度条共享既有 scope、去重身份和活跃桶投影。`latest_bucket_write(view)` 读取 `view["buckets"][-1]` 的缓存写入累计量，仅在最新桶 `quality == "exact"`、桶号与 `write` 都是非负整数时返回 W（包括真实的 0）。缺失/损坏的桶、布尔值、字符串、负值或未知质量返回 `-1`，只隐藏可选角标，不改原有百分比与幅度条。跨桶粗累计无法证明单桶写入量时，不以窗口总量或默认 0 冒充最右桶的真实 W。

`history_fields(view)` 统一正常 bridge 与只读 sidecar 回退的输出：原有 8 个 payload 字段顺序不变，第 9 项为百分比，第 10 项为 12 格 codes，第 11 项为原始 W 或 `-1`。旧 10 字段 helper 只会使角标缺失，原有 UI 仍保留。角标数据不加入 `valid_view()` 的原有有效性条件，也不新增数据库列、持久化 `last_write` 字段、计数器、revision 或投影轴策略；已有同 revision 视图可以直接提供 W，无需产生新请求。

追加宽度按 **`6 + len(write_text)`** 列预算：幅度条后空格 3 列、emoji 2 列、emoji 后空格 1 列，再加格式化数字与单位长度。ANSI 样式不计宽度，`1000k` 等更长文本按实际长度判断；普通与换行布局都只在最后一行能完整容纳时追加。原第二行宽 25 列、文字 `555k` 宽 4 列时，内容宽 34 隐藏、35 恰好显示（默认 host margin 为 4，对应 `COLUMNS=38/39`）。

### 10. SQLite 事务、source checkpoint 与故障回退

默认账本：`${XDG_STATE_HOME:-$HOME/.local/state}/claude-statusline-cockpit/history.sqlite3`。是新命名空间，不迁入旧三列 `CACHE_LOG`。新目录/DB 创建权限分别为 0700/0600，使用 WAL、`synchronous=FULL` 与 `BEGIN IMMEDIATE`。

一次 refresh 的源隔离、各文件入账、pending/counter 更新、恢复诊断、checkpoint、replay 标记和所选 scope 视图在同一事务内提交。任何失败回滚这次事务，不让 offset 超前于已入账记录。首次建表/初始化在 refresh 事务之外；不能把失败 refresh 称为绝不创建状态文件。

每个源路径保存 device/inode、byte offset、行 sequence、已读前缀/尾部 SHA-256。文件未旋转/缩短且边缘 hash 匹配时从 offset 增量追读；否则从头重读并按事件身份去重。只推进到完整换行行，未写完尾行留到下一次；读取中截短则失败。这里是 checkpoint 防护，不是完整文件防篡改校验。

- **JSON 坏完整行**：`json.loads` 异常，整个 refresh 回滚，保留此前画面，修复来源后再读。
- **JSON 合法但合同不满足**：记录 `issues`，不虚构 token；checkpoint 可以越过该行，合同策略重放负责将来恢复。
- **锁/I/O/解析失败**：读取此前同 scope `.view.json`；没有合法保存视图则未知占位。第一/三行仍从 payload 解析。
- **sidecar 发布失败**：候选视图只在 DB COMMIT 后才赋值；已提交数据库不倒退。若随后 sidecar 写入失败，此轮仍可输出已提交 candidate，但 sidecar 可能较旧，下一次源/DB失败会退回该旧画面。

sidecar 不包含对话正文，但包含桶时间、scope、计数与质量，仍是私有使用元数据。写入时加锁、合并各 scope、拒绝同 scope 更低 revision、写临时文件/fsync 后 `os.replace`；数据库事务与 sidecar 发布不是跨文件单一事务。

### 11. 合同策略重放与已证实诊断恢复

`transcript_replays(source_path, policy)` 保存每路径已通过的 `TRANSCRIPT_CONTRACT_POLICY`。缺少策略记录或策略号提升时，该 transcript 从头重放完整行一次，即使旧 checkpoint 已越过拒绝记录。日常后续仍增量读取；策略号只随已验证的合同改变，不随每个 Claude 发布版本改变。

完整五段身份键让重复内容块与已有事件不再加权，不清空 events、pending、counter、quarantine 或审计基线。原 `issues` 保留；仅当路径、行号、原事件时间、project/session/conversation 匹配，且完整身份的最终 R/W/U 已确认入账、未被隔离、诊断 code 属可恢复集合时，才加 `resolved_issues` 证据。冲突 final usage、未来事件等不会自动消解。

恢复后的原诊断不再污染新桶 quality。即使请求之前已经存在、重放只消解诊断而没有增加 token，也提升投影 revision 使视图失效。**revision 是投影修订号，不是请求条数**。replay 标记、恢复证据与 checkpoint 共用事务，失败全部回滚，不能“标记已恢复却没计到用量”。

2026-10-03 上轮本地核验曾补回 **5 个唯一请求**，9 个原内容块没有重复计费；保留 **9 条原诊断**并标记恢复。安装核验 **83.0%** 是当时窗口快照，不是固定统计或本轮实测。完整本机报告保留在本地，不作为公开仓库产物。

### 12. 可选 producer feed 与保守 counter

`CACHE_EVENT_FILE` 可启用明确由集成方生成的 `cockpit-events/v1` JSONL；这不是宣称 Claude Code 原生会输出该格式。参见 `tests/fixtures/event-schema-example.json`。

记录需有 `schema`、source/source_version、project/session/conversation/event 身份、带时区 `event_time` 和 R/W/U；`source_sequence` 未给时使用文件行号，流生产者应显式保持 sequence 单调。`kind` 默认为 `request`，也支持 `stream_start`、`stream_delta`、`stream_stop` 与 `counter`。

不要把 transcript 已覆盖的请求再换一个 source 名镜像输入：不同 source 有意分离，不做相似文本/时间的启发式合并，会计两份。

counter 还需要 `counter_epoch` 与完成 `request_count`。基线按 source/project/session/conversation/epoch 分开；首条仅设基线，不以 `counter-0` 造用量。同 epoch 下降/乱序拒绝，已知重置必须用新 epoch；重复快照不改基线时间；计数增加才生成差值。与请求并用时也不得重复覆盖同一用量。

同桶差值可分配到桶；跨桶粗差值无法知道每桶 token 分配，保留一个不确定端点槽并将相关选中桶标为 unknown，不补空闲格、不按时长插值。整体只在尚无历史被排除或区间完全落入所选窗口时计整个差值一次；部分交叠不凭空按比例拆分，结果带内部质量限制。

### 13. 路径、Git、宽窄输出与色彩分支

目录取 `workspace.current_dir`，再 fallback 到 payload `cwd`；HOME 前缀缩写为 `~`。普通仓库一次 `git status --porcelain -b` 同时读分支与 dirty（tracked/untracked 由 Git 的 porcelain 输出决定），有额外状态行显示 `*`。禁用 optional locks，减少重绘时索引副作用。detached HEAD 另用一次 `rev-parse --short HEAD`；无提交分支/非仓库/失败时不显示伪分支。

`COLUMNS` 有效正整数用于布局，缺失或非法回到 80。第一行自适应舍弃可选段；第二行先扣 `CACHE_HOST_MARGIN`（默认 4，源于特定 host 的边缘裁剪测量），宽时同排，窄时前缀与 12 格分行，极窄继续逐字形换行，保留全部格与数字配色。第三行不做同等宽度截断，长路径可能由 host 换行/截断。

`COLORTERM=truecolor/24bit`、`TERM_PROGRAM=iTerm.app/WezTerm` 或 `TERM` 匹配 `*-direct/*-truecolor` 选真彩，否则用 256 色。只按这些声明判断，不主动探测终端；256 色有限，若干高度档同色是现有调色板限制。字体、Unicode 单格宽度及宿主 footer 的实际裁剪需单独核验。

### 14. 保守维护与审计

`maintain()` 仅执行 WAL 被动 checkpoint、optimize 和 VACUUM；不按 TTL、tail-N 或时间清除 events、身份、counter 基线、checkpoints、pending、隔离记录及旧 scope 投影。代价是磁盘增长与重建视图扫描成本，换取重放去重和冻结历史的可恢复性。

`--inspect` **不是只读诊断**：它会实例化 `History`、初始化 schema/WAL 并在事务内生成视图；`--maintain` 更会写库。生产只读诊断请直接用 SQLite URI `mode=ro`，不得把这两个命令或正常 bridge 当成只读操作。严格隔离的 DB 副本上可运行它们，不能拿副本覆盖实时账本。

## 安装、升级与安全回退

### 依赖与完整安装

要求 Python **3.9+**（含 stdlib `sqlite3`）、macOS Bash **3.2+** 或兼容 Bash、`awk`、`git` 及脚本使用的常规 `cat`/`sed`/`env`。没有外部 Python 包。已核验 macOS；Linux 分支尚不能当作已实测。

先在克隆后的项目目录运行测试，再按需**手动**安装/升级：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
# 测试通过后再安装；这一步会改写当前 Claude 配置
CACHE_HISTORY_PYTHON="$(command -v python3)" bash ./install.sh
```

只复制 `statusline-command.sh` 不够。安装器检测指定解释器版本/SQLite/helper imports，在任何目标写入前验证 settings 是 JSON object，拒绝 settings 与 bundle symlink；锁住安装流程，在同一配置目录 staging 完整四文件运行包并写 `.python-path`。

随后把旧包移为带时间戳备份，`os.replace` 激活新包，再替换已合并的 settings。仅更新 `statusLine.type/command`，保留其他设置；`command` 对包路径进行 shell 引号处理。旧入口脚本、旧日志、历史数据库不删除。

这里的“原子激活”指**包目录替换与 settings 文件替换各自原子**，不是二者合成一个全局原子事务。捕获到异常时恢复旧包/settings，保留失败包；进程断电/强杀落在两步之间仍应检查备份与配置。本项目不自动清理安装备份，也不升级或重启 Claude Code。

安装会固定经过验证的解释器绝对路径，避免交互 shell/Conda/PATH 与状态栏 host 不同。pin 路径是数据，引用执行、不 `eval`。若固定解释器已删除，需验证替代运行时后显式重新安装或用 override，而不是擅自改 pin。

### 升级前后

1. 私有备份当前完整包、settings、pin、仓库未提交/未跟踪成果；若备份 DB，使用一致性 SQLite backup，不能只复制正在 WAL 写入的主文件。
2. 保留运行账本，不删数据库“刷新统计”；部署完整包而非单文件。
3. 升级合同策略时自动一次性重放并去重；普通兼容版本更新无需白名单编辑。
4. 用隔离 fixture 验证新包，再由用户正常触发 host 状态栏更新；不为刷新发送 `hi` 或模型请求。

### 回退原则

安装器返回 `backup_bundle` 与 `backup_settings`（原来有目标时），这些是回退材料，不是自动恢复命令。

- 回退前先备份**当前**状态，检查目标是否有后续编辑；有则手工合并，不能无条件覆盖。
- 只恢复经确认的代码/配置变化，保证恢复包的 helper 与 Python pin 配套；不要用 `git reset --hard` 清掉现有工作。
- **绝不能用旧 DB 快照覆盖实时账本**，否则丢失后续请求。数据库副本只用于审计/演练。
- 旧代码未必理解新诊断/隔离策略。没有证明账本前向兼容时不要直接指向实时库；先用隔离副本测试，维护必要的投影失效与隔离逻辑。否则旧适配器可能重新误计客户端错误或拒绝已恢复记录。

## 配置项

环境变量由启动状态栏的 host 继承；不是安装器把全部变量写进 settings。

| 变量 | 默认/优先级 | 用途与边界 |
| --- | --- | --- |
| `CLAUDE_CONFIG_DIR` | `$HOME/.claude` | 安装目标及默认 transcript 根的父目录 |
| `CACHE_HISTORY_PYTHON` | 优先于 `.python-path` 与 `python3` | 安装/运行解释器；推荐经过验证的绝对路径 |
| `CACHE_TRANSCRIPT_ROOT` | `$CLAUDE_CONFIG_DIR/projects` | 扫描指定 main/subagent 目录结构；不是递归任意路径 |
| `XDG_STATE_HOME` | `$HOME/.local/state` | 默认账本父目录 |
| `CACHE_HISTORY_DB` | `$XDG_STATE_HOME/claude-statusline-cockpit/history.sqlite3` | 自定义 DB；sidecar 为 `<db>.view.json` |
| `CACHE_EVENT_FILE` | 未启用 | 单个显式 `cockpit-events/v1` feed；配置后文件缺失可使本次 refresh 失败 |
| `CACHE_SCOPE_PROJECT` | 空：不限制 | 精确 project 身份过滤 |
| `CACHE_SCOPE_SESSION` | 空：不限制 | 精确 session 身份过滤 |
| `CACHE_SCOPE_CONVERSATION` | 空：不限制 | `main` 或具体 `subagent:<stem>`；与其他 scope 条件 AND |
| `COLUMNS` | 无效时 80 | 终端可见列数；不是 ANSI 字节长度 |
| `CACHE_HOST_MARGIN` | 4 | 第二行两侧合计预留列，允许 0；非法回到 4 |
| `CACHE_HISTORY_DEBUG` | 关闭；`1` 开启 | stderr 的 helper/source/storage 调试；不向 stdout 混入日志 |
| `CACHE_HISTORY_NOW` | 真实读取时钟 | 仅离线测试用，单位秒（可含小数），影响未来事件校验，不推进轴；生产勿设置 |
| `COLORTERM` / `TERM_PROGRAM` / `TERM` | host 提供 | 真彩分支选择；否则 256 色 |

不存在决定命中/移动的 TTL 配置，也不从旧 `CACHE_LOG` 读取历史。

下面可安全在临时目录体验源版：没有真实 transcript、feed、配置或生产 DB，退出后临时目录留存供检查。

```sh
scratch=$(mktemp -d)
printf '%s\n' '{"model":{"display_name":"Claude Test"},"effort":{"level":"high"},"context_window":{"total_input_tokens":0,"context_window_size":200000,"used_percentage":0},"cost":{"total_cost_usd":0}}' |
  CLAUDE_CONFIG_DIR="$scratch/config" CACHE_TRANSCRIPT_ROOT="$scratch/no-transcripts" \
  CACHE_HISTORY_DB="$scratch/history.sqlite3" CACHE_EVENT_FILE='' \
  CACHE_SCOPE_PROJECT='' CACHE_SCOPE_SESSION='' CACHE_SCOPE_CONVERSATION='' \
  CACHE_HISTORY_NOW='' CACHE_HISTORY_PYTHON="$(command -v python3)" COLUMNS=80 \
  bash ./statusline-command.sh
```

scope 配置示意：`CACHE_SCOPE_SESSION=session-example CACHE_SCOPE_CONVERSATION=main`。它只改变第二行过滤，实际 session 标识须来自源记录；不要公开完整本机配置。

## 测试与演示

### 隔离回归

从仓库目录执行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
bash -n statusline-command.sh install.sh
# 安装了 Node 时可额外检查现有演示脚本
node --check demos/cache-demo.js
git diff --check
```

普通测试使用临时 fixture、临时 DB 和临时 config，不需生产安装，也不请求模型。覆盖 token 加权/八档边界、同桶更新与跨空闲单步、去重、未完成流/真零/客户端错误、counter、scope、checkpoint、失败回滚、sidecar、安装失败恢复、Python pin/PATH、宽窄输出与原 UI。

`tests/test_write_badge.py` 补充角标专属回归：同桶 W 累加与去重、shared/筛选 scope、空闲冻结、真零/未知、旧视图与 10 字段 helper、sidecar 回退、k/M 舍入、逐字符颜色与加粗、三个普通空格且无竖线、精确宽度边界及全部原有 UI 保留。原有百分比与 12 格断言继续保留，不以新角标替代旧回归覆盖。

**2026-10-05 本轮验证：**在隔离 HOME/config/transcript/DB 环境下，Python 3.14 运行 **120 项测试：119 通过，1 跳过**；跳过项为该临时 HOME 中未安装的 Conda Python 运行时测试。`bash -n` 与 `git diff --check` 通过。部署前与部署后各做 **140 组**新旧渲染比较，覆盖真彩/256 色、不同数值、已知/未知质量和宽窄边界；部署后使用实际配置的 statusLine 命令，但数据路径仍完全隔离。最终定稿已验证为第二行无竖线、幅度条后恰为 3 个 U+0020 空格。对比覆盖移除分隔符后能在更窄宽度完整显示的角标，以及空间不足/质量未知时整段隐藏的情况；数字和单位的颜色与加粗、其他 UI 字节保持不变。部署仅原子替换状态栏脚本，后端、settings 和 Python pin 未改动；未打开生产数据库，也未通过启动、重启或向 Claude Code 发消息来核验。

2026-10-03 的 **106 项测试全部通过**记录，以及上一轮 Python 3.14.7、系统 Python 3.9.6、Conda Python 3.9.13 的 106 项回归，是历史结果，不是新增角标后所有环境都重新验证的声明。详细日志留在仓库外的私有备份。本轮隔离渲染不是实时终端截图，也不代表所有 Python/Claude/Linux 组合已测；未来版本号测试仍是保持合同的模拟 fixture，不是宣称那些客户端已发布或已做现场验证。

`tests/verify_local_transcripts.py` 和 `tests/verify_claude_host.py` 属本地专项核验；不在普通 `test_*.py` discovery 内。它们可能涉及真实路径/host/本机状态，不能当作无条件只读通用命令，须先阅读脚本并确认隔离与授权。不要将其输出、对话、DB 或配置发布。

### 浏览器演示

- `demos/cache-bar-demo.html`：跨桶/窗口边界与重复重绘。
- `demos/cache-bar-live-demo.html`：流、完成与空闲冻结。
- `demos/cache-bar-multi-segment-demo.html`：跨会话和 token 加权。

`tests/build_demo_vectors.py` 用真实 Python/SQLite 算法生成合成 `history-vectors.json`，浏览器 JS 只呈现预计算视图，不重写账本算法。需要显式刷新夹具时运行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/build_demo_vectors.py
# 然后从项目目录本地提供静态内容；只绑定 loopback
python3 -m http.server --bind 127.0.0.1 8766
```

在本机浏览器打开对应 `/demos/…html`。这些文件验证 fixture 展示，不验证真实 Claude footer 的裁剪与刷新。本轮文档整理未重新生成向量、未重新设计 demos。

## 限制、故障排查与隐私边界

- **低柱不一定异常。** ≤80% 都压到最低档；整体数字与最右桶可以不同；同桶入账未跨档时柱不变；没有新用量冻结是正常行为。
- **未知占位有意复用原 UI。** `0.0%`/`▁` 不能证明真实零命中；需私有诊断中的 quality/原诊断确认完整性，不能为此擅自加新符号。
- **本地 transcript 合同不是通用稳定 API。** 版本仅元数据；真实必需语义改变要改已验证合同、提升策略号并回放，不凭空接受所有未来格式。
- **源发现与性能有边界。** 只扫规定路径；删除或不可读的历史源无法凭空恢复。日常源增量读取，但视图重建会读取 scope 的保留事件、发现会枚举文件；没有有界存储/常数成本承诺。
- **损坏完整 JSON 行阻断整个 refresh。** 旧画面会保留而非部分提交其他文件；不能通过删 checkpoint/DB 来绕过证据。
- **所有 Python 都失败时元数据无法解析。** pin 解决运行时选择，不是替代 JSON 解释器。不要在生产运行 `History`/bridge/`--inspect` 进行所谓只读诊断。
- **视觉/host 边界。** 第二行逐格保留不等于宿主绝不裁剪；第一行字符长度未实现完整 Unicode 终端宽度算法，第三行长路径未主动截短。默认 margin 来自特定 host 测量，不保证自定义 padding/所有版本。
- **保留但未顺手修的旧表述。** demos 的 legend 仍有 `? unknown`/`~ partial evidence` 文字，但 JS 行渲染已经隐藏这些符号；shell 还有“right-aligned cost”的旧注释，实际费用按当前代码紧跟前段。这些不作为现状说明，本轮不动 UI/注释代码。
- **本机数据不得发布。** `.gitignore` 排除 `verification/`、字节码、测试残留、数据库/sidecar、转录、备份、pin 与本机配置。忽略规则不是隐私扫描、也不能自动取消已跟踪文件；发布前人工检查 staging 与可发布源码/合成 fixture。不把整个工作目录打包上传。

更多维护不变量、文档与代码核对点见 `STATUSLINE-NOTES.md`。本项目以 MIT License 发布，见 `LICENSE`。
