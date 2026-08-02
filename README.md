<div align="center">

# astrbot_plugin_maimai

_✨ 舞萌 DX · 查歌查分 · B50 · 推分成长 · 成绩同步 ✨_

[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-1.8.1-brightgreen.svg)](https://github.com/BCXW-0/astrbot_plugin_maimai)
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
- `1.7.x`：`舞萌体检`、`舞萌初始化`、分层帮助、我的舞萌、目标推分与打卡；`1.7.2` 锐评含金量；`1.7.3` 标签权重
- `1.8.x`：本地 maidata 结构标签；`1.8.1` 校准管子=hold、双押/定位局部峰值，并导出高可信网页金标训练集

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
| `chart_tag_llm_provider_id` | 本地谱面分析专用模型 Provider ID；留空跟随当前模型 | 空 |
| `chart_tag_llm_timeout_seconds` | 本地谱面分析单次模型调用超时（秒） | `150`（5-600） |
| `chart_tag_llm_concurrency` | 本地谱面分析同时调用模型的数量；限流时设为 `1` | `4` |
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
| `新手入门` | 绑定与首次使用引导 |
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

### 推分与练习

| 命令 | 说明 |
|:----:|:-----|
| `吃分推荐` [目标] | 智能吃分，可带目标 Rating |
| `冲 15000` / `冲分 15000` | 按目标推分 |
| `今日推分` / `今日3首` | 今日练习清单 |
| `打卡 <ID>` / `练习记录` | 本地练习打卡 |
| `我要在13+上10分` | 按等级找可涨分谱 |
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

用途：锐评人格、加权谱面标签任务、配置摘要；指令触发方式以 [`static/help.txt`](static/help.txt) 为准。
监听非本机地址时必须配置 Token。

## 谱面标签

覆盖 Expert / Master / Re:Master（定数 ≥ 12.6），带难点权重；高辨识配置优先，底力/手速等泛化标签降权。

### 本地 maidata 结构分析（1.8.0+）

1. 从 [OneCat 官谱](https://dw.moant.cn:34225/onecat/#/official) 仅下载 `maidata.txt`（不下载 BGA）
2. 按 simai 语法解析各难度事件
3. 用 BPM、密度、键位几何、滑键形状与 **hold 链** 判定配置
4. 写入 `local_tags`；高置信时优先于联网文案，并保留本地主配置顺序

圈内校准（1.8.1）：

- **管子** = hold（短 hold 局部过密，或 hold 结束→下一 hold 间隔极短的链式管子），不是滑键
- **双押** = 短时同时击峰值/链式（非重叠 1s 绝对次数 + 链长），不是全谱平均，也不看易饱和的 ratio
- **定位** = 短时高密 + 大位移卡手，或难划星星局部

### 训练金标（网页高可信映射）

- 从多源攻略正文互证 / 人工标签抽取低歧义标签，作为规则与后续模型的元数据
- 输出：`static/chart_tag_training_labels.jsonl`
- 排除纯物量摘要噪声；歧义标签（底力/手速等）需更强证据

数据文件：`static/maimaidx_chart_tags.json`（含 `tag_scores` / `local_*`）。可 WebUI 手动覆写。

### 本地谱面一键分析

WebUI 的“谱面标签”页提供“本地谱面一键分析”：默认读取插件内的相对路径 `static/Levels`，只处理定数不低于 `12.6` 的难度。目录必须位于插件根目录内，避免通过路径参数读取插件外文件。

任务会解析每个文件的 `lv_2` 至 `lv_6` 与 `inote_2` 至 `inote_6`，按实际 BPM 生成连续两小节窗口，并将结构摘要交给 AstrBot 对话模型。标签只表示反复出现并构成谱面主要游玩压力的难点，不表示配置曾经出现过；模型最多返回 5 个主要难点。结果和窗口证据写入 `static/maimaidx_chart_tags.json`；任务进度写入 `static/maimai_levels_llm_job.json`。可在 WebUI 中设置专用 Provider、单次调用超时、最低定数、文件数限制、难度数限制、强制重算和停止任务。阶段运行结果记录在 `ZHUANGWEI_ANALYSIS_REPORT.md`。

撞尾专项分析使用严格规则：Slide 经过区域与目标音符的时间差必须满足 `0 < delta < 0.2s`，等于边界值不计入，目标原始语法含 `x` 的 Ex 音符排除。固定样本清单、完整事件、Slide 路径、候选位置和模型证据保存在 `static/chart_tag_llm_sample_manifest.json` 与 `static/chart_tag_llm_training_dataset.jsonl`；本地模型及逐 epoch Loss 保存在 `static/maimai_chart_tag_local_model.npz`、`static/maimai_chart_tag_local_model.json` 和 `static/chart_tag_llm_training_loss.json`。模型审核前 `formal_pipeline_enabled=false`，不会接管正式打标。

## 数据与安全

| 路径 | 说明 | 是否提交 |
|:-----|:-----|:--------:|
| `static/music_*.json` | 曲库 / 谱面 / 别名缓存 | 否 |
| `static/user_import_tokens.json` | 用户 Import-Token | 否 |
| `static/arcade_credentials.json` | 机台凭据 | 否 |
| `static/user_practice_log.json` | 练习打卡 | 否 |
| `static/mai/` | 曲绘与表图资源 | 完整包不建议提交 |

- Developer-Token 走插件配置，不要写死仓库
- 含 SGID 的同步建议私聊
- 本插件免费开源，不包含任何付费能力

## 致谢

- [ZhiheZier/astrbot_plugin_maimaidx](https://github.com/ZhiheZier/astrbot_plugin_maimaidx)
- [水鱼查分器](https://www.diving-fish.com/maimaidx/prober/)
- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
