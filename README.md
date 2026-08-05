<div align="center">

# astrbot_plugin_maimai

_✨ 舞萌 DX · 查歌查分 · B50 · 推分成长 · 成绩同步 ✨_

[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-2.5.0-brightgreen.svg)](https://github.com/BCXW-0/astrbot_plugin_maimai)
[![GitHub](https://img.shields.io/badge/作者-BCXW--0-blue)](https://github.com/BCXW-0)

</div>

基于 [ZhiheZier/astrbot_plugin_maimaidx](https://github.com/ZhiheZier/astrbot_plugin_maimaidx) 二次开发。  
版本记录见 [`CHANGELOG.md`](CHANGELOG.md)，完整指令触发方式与一句话说明见 [`static/help.txt`](static/help.txt)。

## 介绍

面向 AstrBot 的国服舞萌 DX 插件：查歌、查分、B50、牌子/定数表、吃分推荐、成绩同步、锐评 B50、猜歌，以及管理员体检与一键初始化。

相对上游主要变化：

- 保留查歌 / 查分 / B50 / 定数表 / 完成表 / 猜歌
- 移除别名投票、别名推送、机厅排卡
- 增加水鱼 Import-Token 绑定、SGWCMAID 同步、吃分推荐、锐评人格 WebUI、谱面标签
- `1.7.x`：`舞萌体检`、`舞萌初始化`、分层帮助和我的舞萌；`1.7.2` 锐评含金量；`1.7.3` 标签权重
- `2.2.0`：按 XLS 规则全量重算 12.6 - 15.0 谱面，训练本地模型；标签按请求从 Levels 直接分析，不再保存运行时标签库，并新增插件日志模块
- `2.3.0`：清理不再维护的成长辅助、目标查询和本地记录功能，保留吃分推荐的目标 Rating 参数
- `2.4.0`：优化谱面标签的增量索引、特征复用、轻量检索、B50 后台分析和任务缓存；不改变现有指令、WebUI 操作或输出字段

> 纯净仓库不含完整 `static/mai/` 资源包，部署后需自备静态资源并执行初始化。

## 安装

### 插件市场

搜索 `astrbot_plugin_maimai` 或 `舞萌`，安装并启用。

### Git

```bash
git clone https://github.com/BCXW-0/astrbot_plugin_maimai.git astrbot_plugin_maimaidx
```

依赖（若未自动安装）：

```bash
pip install -r requirements.txt
python -m playwright install chromium   # B50 / ginfo 等出图需要
```

## 快速开始（管理员）

1. AstrBot 主配置填写 `admins_id`（超级管理员 QQ）
2. 插件配置按需填写 `bot_name`、`maimaidxtoken`（水鱼 Developer-Token）
3. 准备 `static/mai/` 静态资源与可选 `static/help.png`
4. 重载插件后私聊执行：

```text
舞萌体检
舞萌初始化
```

`舞萌初始化` 会**一次性**完成并热加载：

1. 曲库与拟合定数  
2. 别名库  
3. 定数表图片  
4. 完成表图片  

> `更新maimai数据` / `更新别名库` / `更新定数表` / `更新完成表` 已不再执行独立更新逻辑；兼容入口会提示改用 `舞萌初始化`。

## 配置

| 配置项 | 说明 | 默认 |
|:------:|:-----|:----:|
| `bot_name` | 机器人显示名 | 空 |
| `enable_reply` | 回复是否带引用 | `true` |
| `maimaidxtoken` | 水鱼 Developer-Token（非 Import-Token） | 空 |
| `roast_b50_provider_id` | 锐评专用模型 Provider ID | 空 |
| `roast_persona_prompt_sample_limit` | 锐评人格样本上限 | `120` |
| `roast_persona_webui_enabled` | 插件 WebUI | `false` |
| `roast_persona_webui_host` / `port` / `token` | WebUI 监听与访问令牌 | `127.0.0.1` / `8796` / 空 |
| `sgid_max_age_seconds` | SGID 有效窗口（秒） | `600` |
| `request_timeout_seconds` | 更新 b50 超时（秒） | `30` |
| `maimai_http_proxy` | 更新 b50 HTTP 代理 | 空 |
| `daily_update_alias` | 每日维护是否刷新别名 | `true` |
| `daily_update_tables_if_empty` | 表图缺失时自动补全 | `true` |
| `daily_update_hour` | 每日维护小时（0-23） | `4` |

Import-Token 请用户自行 `绑定水鱼`，不要写进仓库。

## 命令

### 管理（超级管理员）

| 命令 | 说明 |
|:----:|:-----|
| `舞萌体检` / `舞萌状态` | 检查曲库、别名、表图、Token、Playwright、WebUI |
| `舞萌初始化` / `一键更新舞萌` | 一键更新曲库+别名+定数表+完成表并热加载 |
| `开启舞萌功能` / `关闭舞萌功能` | 当前群开关 |

### 基础 / 帮助

| 命令 | 说明 |
|:----:|:-----|
| `帮助` / `help` | 高频帮助 + 可选帮助图 |
| `帮助 查分/推分/同步/猜歌/管理` | 分层说明 |
| `我的舞萌` | Rating、B35/B15、绑定、下一首建议 |
| `今日舞萌` / `jrys` | 今日运势 |
| `来个13+` / `mai什么` | 随机 / 推分语义推荐 |

### 查歌

| 命令 | 说明 |
|:----:|:-----|
| `查歌 <关键词>` | 标题 / 别名搜索 |
| `id <歌曲ID>` | 按 ID 查详情 |
| `定数查歌` / `bpm查歌` / `曲师查歌` / `谱师查歌` | 条件搜索 |

### 成绩

| 命令 | 说明 |
|:----:|:-----|
| `b50` / `ccb` | Best 50 |
| `info` / `minfo` | 单曲成绩 |
| `ginfo` | 全局谱面统计 |
| `分数线` / Rating 计算 | 容错与分数换算 |
| `查看排名` / `我的排名` | 公开榜 |

### 推分

| 命令 | 说明 |
|:----:|:-----|
| `吃分推荐` [目标] | 智能吃分，可带目标 Rating |
| `祭将进度` 等 | 牌子 / 等级进度 |
| `13+定数表` / `祭将完成表` | 表图查询（需先初始化生成） |
| `锐评b50` | LLM 锐评（水鱼拟合定数含金量 + 圈内黑话） |

### 同步

| 命令 | 说明 |
|:----:|:-----|
| `绑定水鱼 <Import-Token>` | 绑定成绩上传 token（建议私聊） |
| `查看水鱼` / `解绑水鱼` | 查看 / 解绑 |
| `更新b50 <SGWCMAID>` / `导 <SGWCMAID>` | 机台同步；后续可直接 `更新b50` |

### 猜歌

| 命令 | 说明 |
|:----:|:-----|
| `猜歌` / `猜曲绘` | 开始 |
| `重置猜歌` | 重置 |
| `开启mai猜歌` / `关闭mai猜歌` | 群开关 |

## WebUI（可选）

开启 `roast_persona_webui_enabled` 后访问：

```text
http://127.0.0.1:8796/?token=你的token
```

用途：锐评人格、谱面标签、插件日志和配置摘要；谱面标签只在 WebUI 中操作，指令触发方式以 [`static/help.txt`](static/help.txt) 为准。
监听非本机地址时必须配置 Token。

### WebUI 谱面标签

在「谱面标签」页可以：

- 按 `10.0 - 15.0` 定数范围下载 OneCat 官谱，默认 `12.6 - 15.0`；支持全部下载、仅未下载和搜索下载。
- 只保存谱面 `.txt` 到 `static/Levels`，不保存 BGA、音频、宴谱或其他资源，并按谱面内的 `&shortid` 与 `&title` 命名。
- 使用 `static/maimai_chart_tag_model.npz` 执行强制重算或新谱计算；标签分析范围固定为 `12.6 - 15.0` 的 Expert / Master / Re:Master。
- 按 ID、曲名、艺术家、谱师或定数查看本地模型标签、概率、分析窗口和运行时来源映射。
- 下载任务会在 OneCat 完成筛选后显示候选歌曲总数、当前处理数和当前谱面；任务启动阶段会先显示等待筛选状态。
- “插件日志”页只展示本插件日志，支持级别筛选、关键词筛选、自动刷新和清空。

谱面标签不保存为静态标签库。每次请求先按 `shortid:level_index` 在 `static/Levels` 中找到 `shortid_title.txt`，解析对应的 `&inote_<diff_id>`，再交给本地模型；搜索使用轻量标签摘要，详情请求仍生成完整分析窗口和撞尾证据。结果只保留在当前进程内存中，并使用有上限的缓存。映射同时携带文件相对路径、文件 SHA-256、曲名、艺术家、定数、BPM、谱师和 `mapping_id`，不会依赖文件名猜测难度。

## 谱面标签模型

覆盖 Expert / Master / Re:Master（定数 ≥ 12.6），带难点权重；高辨识配置优先，底力/手速等泛化标签降权。

### 本地模型谱面审计

训练元数据直接读取插件内的 `static/Levels`，只分析 `lv_4`、`lv_5`、`lv_6` 中定数 `12.6 - 15.0` 的难度。WebUI、吃分推荐和锐评 B50 的谱面标签都使用同一个本地模型；OneCat 仅用于下载谱面文件。

规则引擎按 XLS 的候选特征、难点特征和同定数占比上限工作，使用连续两小节局部窗口，输出最多 5 个主要标签，并为每个标签保存窗口、事件原始语法或撞尾候选位置。数据集保留完整 `inote`、事件序列、BPM 变化、定数、文件 SHA-256、排除的 Ex 目标和标签位置，便于逐条审阅。模型训练目标也先按谱面特征分数限制为最多 5 个标签；同定数占比裁剪只用于审计展示，不会制造训练负样本。

撞尾依据三篇撞尾/无理配置文章和 [simai 语法说明](https://w.atwiki.jp/simai/pages/1002.html)：以目标时间减去 Slide 进入路径区域时间的有符号 `delta` 判定，危险范围为 `[-0.05s, +0.20s]`；`delta=0` 为绝对撞尾，`0<delta<=0.15s` 为硬撞尾，两侧边缘为软撞尾；最后 A 区覆盖到 Slide 结束并保留后 0.20 秒。原始语法含 `x` 的 Ex 目标单独记录并排除，孤立软边界不直接打标。

运行：

```bash
PYTHONPATH=.. python3 -m astrbot_plugin_maimaidx.libraries.chart_tags.training_dataset \
  --reviews /path/to/model_reviews.json \
  --levels static/Levels
```

产物：`static/chart_tag_manifest.json`、`static/chart_tag_dataset.jsonl.gz`、`static/chart_tag_audit.json.gz`、`static/chart_tag_loss.json`、`static/chart_tag_training_run.json`、`static/maimai_chart_tag_model.npz`、`static/maimai_chart_tag_model.json` 和 [`CHART_TAG_REPORT.md`](CHART_TAG_REPORT.md)。压缩文件保留完整训练记录，报告包含候选/最终标签使用率和全部有效难度的逐谱面标注。

当前训练产物使用 125 条带可追溯媒体来源的高置信样本（严格 80% 重合样本不足时按重合度降级选择），按歌曲分组划分训练/验证/留出集，并使用五成员集成、早停、类别不平衡权重和逐标签阈值校准。只有验证集 precision ≥ 0.80、留出集 precision ≥ 0.80 且 F1 ≥ 0.40 的模型才会被写入；本次留出集 precision 为 96.30%，F1 为 68.42%。

`static/Levels` 由管理员第一次使用 WebUI 时手动下载，不随仓库提交；训练元数据和模型文件可以提交到仓库。运行时只按谱面文件即时生成标签结果，不保存标签库。

### 训练审核口径

新增训练审核记录必须同时通过当前对话模型 `5.6-Luna Max` 与 AstrBot Provider `google_gemini/gemini-2.5-pro`，只有两者标签集合完全一致的谱面才进入联网证据校验；请求失败、输出包含工具调用内容、不是纯 JSON 或标签不一致，都会被排除。历史上已经通过三模型一致性审核的记录继续兼容保留，不会被当作新的双模型记录。

## 数据与安全

| 路径 | 说明 | 是否提交 |
|:-----|:-----|:--------:|
| `static/music_*.json` | 曲库 / 谱面 / 别名缓存 | 否 |
| `static/user_import_tokens.json` | 用户 Import-Token | 否 |
| `static/arcade_credentials.json` | 机台凭据 | 否 |
| `static/Levels/` | WebUI 下载的本地谱面输入 | 否 |
| `static/chart_tag_manifest.json` | 全量谱面与文件映射清单 | 是 |
| `static/chart_tag_dataset.jsonl.gz` | 含完整谱面内容、事件、窗口、标签位置的训练元数据 | 是 |
| `static/chart_tag_audit.json.gz` | 全量审计记录与标签使用率 | 是 |
| `static/chart_tag_loss.json` / `static/chart_tag_training_run.json` | Loss 曲线、验证结果与训练记录 | 是 |
| `static/maimai_chart_tag_model.*` | 本地标签模型及元数据 | 是 |
| `static/mai/` | 曲绘与表图资源 | 完整包不建议提交 |

- Developer-Token 走插件配置，不要写死仓库
- 含 SGID 的同步建议私聊
- 本插件免费开源，不包含任何付费能力

## 致谢

- [ZhiheZier/astrbot_plugin_maimaidx](https://github.com/ZhiheZier/astrbot_plugin_maimaidx)
- [水鱼查分器](https://www.diving-fish.com/maimaidx/prober/)
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
