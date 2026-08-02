<div align="center">

# astrbot_plugin_maimai

_✨ 舞萌 DX · 查歌查分 · B50 · 推分成长 · 成绩同步 ✨_

[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-2.1.2-brightgreen.svg)](https://github.com/BCXW-0/astrbot_plugin_maimai)
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
- `2.0.0`：移除旧谱面模型接口，改为 Codex 对话内审计并保留完整可追溯训练元数据
- `2.1.1`：合并 WebUI 谱面标签模块，修复自动打标请求鉴权与错误反馈
- `2.1.2`：移除联网标签补缺和手动标签管理入口，谱面标签 WebUI 仅保留本地模型打标

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

用途：锐评人格、谱面标签（本地模型打标与结果检索）和配置摘要；谱面标签只在 WebUI 中操作，指令触发方式以 [`static/help.txt`](static/help.txt) 为准。
监听非本机地址时必须配置 Token。

### WebUI 谱面标签

在「谱面标签」页可以：

- 按 `10.0 - 15.0` 定数范围下载 OneCat 官谱，默认 `12.6 - 15.0`；支持全部下载、仅未下载和搜索下载。
- 只保存谱面 `.txt` 到 `static/Levels`，不保存 BGA、音频、宴谱或其他资源，并按谱面内的 `&shortid` 与 `&title` 命名。
- 使用 `static/maimai_chart_tag_model.npz` 执行强制重算或新谱计算，默认分析 `12.6 - 15.0` 的 Expert / Master / Re:Master。
- 按 ID、曲名、艺术家、谱师或定数查看本地模型标签、概率、分析窗口和来源映射。

标签库的每个本地条目都保留 `mapping`：`tag_file` / `tag_file_key` 明确标签文件及其 `shortid:level_index` 键，`diff_id` 对应谱面中的 `&inote_N`，并同时记录 `static/Levels` 相对路径、源文件 SHA-256、曲名、艺术家、定数、BPM、谱师和 `mapping_id`。顶层 `chart_mapping` 可由标签 key 反查谱面，`chart_file_mapping` 可由一个谱面文件列出其中所有难度和对应标签 key；详情接口还会返回同文件的完整难度索引。下载完成后会先创建待分析条目，分析结果写回同一条目，不依赖文件名猜测曲目。

## 谱面标签模型

覆盖 Expert / Master / Re:Master（定数 ≥ 12.6），带难点权重；高辨识配置优先，底力/手速等泛化标签降权。

### 本地模型谱面审计

训练元数据直接读取插件内的 `static/Levels`，只分析 `lv_4`、`lv_5`、`lv_6` 中定数不低于 `12.6` 的难度。WebUI 谱面标签使用已训练的本地模型，不调用 AstrBot 谱面模型；OneCat 仅用于下载谱面文件。

Codex 对本地解析出的连续两小节窗口进行复核，输出最多 5 个主要标签，并为每个标签保存窗口、事件原始语法或撞尾候选位置。普通 Slide 星头不会进入如龙、爬梯交互和协调序列；爬梯、协调、管子、跳拍和如龙还要通过局部结构门槛。数据集保留完整 `inote`、事件序列、BPM 变化、定数、文件 SHA-256、排除的 Ex 目标和被拒绝的候选标签，便于逐条审阅。

撞尾依据三篇撞尾/无理配置文章和 [simai 语法说明](https://w.atwiki.jp/simai/pages/1002.html)：以目标时间减去 Slide 进入路径区域时间的有符号 `delta` 判定，危险范围为 `[-0.05s, +0.20s]`；`delta=0` 为绝对撞尾，`0<delta<=0.15s` 为硬撞尾，两侧边缘为软撞尾；最后 A 区覆盖到 Slide 结束并保留后 0.20 秒。原始语法含 `x` 的 Ex 目标单独记录并排除，孤立软边界不直接打标。

运行：

```bash
PYTHONPATH=.. python3 -m astrbot_plugin_maimaidx.libraries.chart_tags.training_dataset
```

产物：`static/chart_tag_sample_manifest.json`、`static/chart_tag_dataset.jsonl`、`static/chart_tag_review.json`、`static/chart_tag_loss.json`、`static/maimai_chart_tag_model.npz`、`static/maimai_chart_tag_model.json` 和 [`CHART_TAG_REPORT.md`](CHART_TAG_REPORT.md)。报告包含原始/最终标签使用率和 100 个谱面的逐条打标情况；WebUI 谱面标签使用该模型，并将结果写入正式标签文件的 `model_tags`。

本地模型分析结果写入 `model_tags` 并合并到 `final_tags`，正式标签文件同时保留谱面文件、谱面信息和映射关系；当前 WebUI 不提供联网补缺或手动标签编辑入口。

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
