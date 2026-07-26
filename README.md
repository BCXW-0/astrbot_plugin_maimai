<div align="center">

# astrbot_plugin_maimai

_✨ 舞萌 DX · 查歌查分 · B50 · 推分成长 · 成绩同步 ✨_

[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/Version-1.8.0-brightgreen.svg)](https://github.com/BCXW-0/astrbot_plugin_maimai)
[![GitHub](https://img.shields.io/badge/作者-BCXW--0-blue)](https://github.com/BCXW-0)

</div>

基于 [ZhiheZier/astrbot_plugin_maimaidx](https://github.com/ZhiheZier/astrbot_plugin_maimaidx) 二次开发。  
版本记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 介绍

面向 AstrBot 的国服舞萌 DX 插件：查歌、查分、B50、牌子/定数表、吃分推荐、成绩同步、锐评 B50、猜歌，以及管理员体检与一键初始化。

相对上游主要变化：

- 保留查歌 / 查分 / B50 / 定数表 / 完成表 / 猜歌
- 移除别名投票、别名推送、机厅排卡
- 增加水鱼 Import-Token 绑定、SGWCMAID 同步、吃分推荐、锐评人格 WebUI、谱面标签
- `1.7.x`：`舞萌体检`、`舞萌初始化`（一次完成曲库+别名+定数表+完成表并热加载）、分层帮助、我的舞萌、目标推分与打卡；`1.7.2` 锐评 B50 含金量与提示词重构；`1.7.3` 谱面标签权重与证据源分层

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

> 原独立指令 `更新maimai数据` / `更新别名库` / `更新定数表` / `更新完成表` 已移除；若仍输入旧指令，会提示改用 `舞萌初始化`。

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

用途：锐评人格、加权谱面标签任务、命令说明、配置摘要。  
监听非本机地址时必须配置 Token。

## 谱面标签

### 本地 maidata 结构分析（1.8.0+）

1. 从 [OneCat 官谱](https://dw.moant.cn:34225/onecat/#/official) 仅下载 `maidata.txt`（可关 BGA）
2. 按 simai 语法解析各难度（只分析定数 ≥ 12.6）
3. 用 BPM、密度、键位几何、滑键形状等特征判定配置/难点并加权
4. 写入 `local_tags`；高置信时优先于联网文案标签

> 这是可解释的本地引擎。若准确率稳定，可关闭联网搜标签；后续也可用同一特征训练校准模型。

## 谱面标签

- 覆盖 Expert / Master / Re:Master，且定数 ≥ 12.6
- 标签带**难点权重**，高辨识配置优先，泛化的底力/手速降权
- 来源：Gamerch 谱面说明与物量、B 站/YouTube 攻略证据；可 WebUI 手动覆写
- 数据文件：`static/maimaidx_chart_tags.json`（含 `tag_scores`）

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
