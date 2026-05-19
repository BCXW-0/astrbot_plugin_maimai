<div align="center">

# astrbot_plugin_maimaidx

<p>
  <b>舞萌 DX · AstrBot 插件</b>
</p>

<p>
  查歌 · 查分 · B50 · 锐评 B50 · 成绩同步 · 谱面标签 · 吃分推荐 · 猜歌
</p>

<p>
  <img src="https://img.shields.io/badge/AstrBot-Plugin-7c3aed?style=for-the-badge" alt="AstrBot Plugin" />
  <img src="https://img.shields.io/badge/maimai-DX-ec4899?style=for-the-badge" alt="maimai DX" />
  <img src="https://img.shields.io/badge/Python-3.x-2563eb?style=for-the-badge" alt="Python 3" />
</p>

<p>
  <b>本插件是基于 <a href="https://github.com/ZhiheZier/astrbot_plugin_maimaidx">ZhiheZier/astrbot_plugin_maimaidx</a> 的进一步开发。</b>
</p>

</div>

---

## 项目说明

`astrbot_plugin_maimaidx` 是面向 AstrBot 的舞萌 DX 插件，提供曲目查询、成绩查询、B50 图片、B50 锐评、成绩同步、谱面标签、吃分推荐和猜歌等功能。

本文所有文件路径均以插件根目录 `astrbot_plugin_maimaidx/` 为基准。例如 `static/help.png` 指的是插件目录下的 `static/help.png`，不是系统绝对路径。

当前版本相对上游的主要变化：

- 已移除别名提交、别名投票、别名推送和机厅排卡功能。
- 已保留别名查歌能力，并恢复超级管理员命令 `更新别名库`。
- 增加用户 Import-Token 绑定、SGWCMAID 成绩同步、锐评人格 WebUI、谱面标签和吃分推荐。
- 曲目标签文件固定保存到 `static/maimaidx_chart_tags.json`，任务状态保存到 `static/maimaidx_chart_tags_job.json`。

## 功能概览

| 模块 | 能力 |
|---|---|
| 歌曲查询 | 按关键词、别名、ID、定数、BPM、曲师、谱师查询曲目 |
| 成绩查询 | B50、单曲成绩、全局统计、Rating 排行榜、分数线计算 |
| 成绩同步 | 用户绑定水鱼 Import-Token 后，通过 SGWCMAID 同步机台成绩到水鱼 |
| 锐评 B50 | 结合 B35/B15、达成率、拟合定数、谱面标签、曲师/谱师和人格风格生成锐评图 |
| 谱面标签 | 通过 WebUI 生成基础标签文件，并批量搜索资料后调用 LLM 抽取谱面标签 |
| 吃分推荐 | 按玩家 B50 标签倾向、B35/B15 地板和拟合定数推荐可吃分谱面 |
| 猜歌 | 群聊曲目猜歌、曲绘猜歌、答案判定和群开关 |
| 插件 WebUI | 管理锐评人格、谱面标签、命令说明、配置摘要和部分运行时配置 |

## 准备工作

以下步骤面向 Bot 管理员。建议先完成配置和静态资源，再启用插件或重载 AstrBot。

### 1. 安装依赖

如果 AstrBot 环境没有自动安装插件依赖，请在插件目录执行：

```bash
pip install -r requirements.txt
```

如生成图片或表格时报 Chromium / Playwright 相关错误，再执行：

```bash
python -m playwright install chromium
```

### 2. 配置超级管理员

管理类命令依赖 AstrBot 主配置中的 `admins_id`。请在 AstrBot 主配置里加入 Bot 管理员 QQ 号，而不是写入插件配置文件。

需要超级管理员权限的常用命令包括：

```text
更新maimai数据
更新别名库
更新定数表
更新完成表
开启舞萌功能
关闭舞萌功能
```

### 3. 准备水鱼 Developer-Token

在 AstrBot 插件配置界面填写：

```text
maimaidxtoken
```

这是水鱼开发者接口 Token，用于 B50、牌子、单曲成绩和开发者接口查询。它不是用户个人 Import-Token。

建议优先通过 AstrBot 插件配置或插件 WebUI 保存 Token，不要把真实 Token 提交到仓库。`static/config.json` 可以保留为空模板。

### 4. 准备静态资源

纯净仓库不包含完整曲绘、牌子、Rating 和绘图素材。首次部署或资源缺失时，请下载资源包：

```text
https://cloud.yuzuchan.moe/f/nXt6/Resource.7z
```

解压后覆盖插件的 `static` 目录，例如：

```bash
7z x Resource.7z -y -o./static
```

覆盖后应至少存在：

```text
static/mai/
static/mai/pic/
static/mai/cover/
static/mai/rating/
static/mai/plate/
```

当前插件不提供群聊内更新静态资源的命令。需要更新曲绘或素材时，请重新下载资源包并覆盖 `static` 目录。

### 5. 执行初始化指令

首次部署、更新曲库、更新别名或补齐表格资源时，建议由超级管理员依次执行：

```text
更新maimai数据
更新别名库
更新定数表
更新完成表
```

| 指令 | 作用 |
|---|---|
| `更新maimai数据` | 刷新曲库和谱面统计缓存，包括拟合定数等数据 |
| `更新别名库` | 刷新别名查歌使用的别名数据 |
| `更新定数表` | 生成或更新定数表图片 |
| `更新完成表` | 生成或更新完成表图片 |

### 6. 初始化谱面标签

锐评 B50 和吃分推荐会读取谱面标签。首次部署后，建议在插件 WebUI 的「谱面标签」页执行：

```text
生成基础标签文件
启动自动更新
刷新状态
```

谱面标签相关文件保存位置：

```text
static/maimaidx_chart_tags.json
static/maimaidx_chart_tags_job.json
```

请注意：本插件本地与仓库文档均以 `static/` 作为曲目标签 JSON 的保存目录。

### 7. 准备帮助图

`帮助` / `help` 命令会发送：

```text
static/help.png
```

建议管理员按群规、Bot 名称、WebUI 地址和常用命令自行设计并替换这张图。

### 8. 启用插件 WebUI

如需使用锐评人格、谱面标签和配置管理，请在 AstrBot 插件配置界面设置：

```text
roast_persona_webui_enabled
roast_persona_webui_host
roast_persona_webui_port
roast_persona_webui_token
```

访问示例：

```text
http://127.0.0.1:8796/?token=你的token
```

如果 WebUI 对外网开放，务必配置 `roast_persona_webui_token`。

## Bot 管理员使用说明

### 配置项

| 配置项 | 说明 |
|---|---|
| `bot_name` | Bot 显示名称，用于今日运势和图片页脚等文本 |
| `enable_reply` | 是否在多数回复中附带引用消息 |
| `maimaidxtoken` | 水鱼 Developer-Token |
| `roast_b50_provider_id` | 锐评 B50 专用模型 Provider ID，留空则使用当前默认模型 |
| `roast_persona_prompt_sample_limit` | 每次锐评注入的人格样本上限 |
| `roast_persona_webui_enabled` | 是否启用插件 WebUI |
| `roast_persona_webui_host` | WebUI 监听地址 |
| `roast_persona_webui_port` | WebUI 监听端口 |
| `roast_persona_webui_token` | WebUI 访问 Token |
| `sgid_max_age_seconds` | SGWCMAID 有效窗口，默认建议 180 秒 |
| `request_timeout_seconds` | 成绩同步网络请求超时 |
| `maimai_http_proxy` | 成绩同步访问官方数据源时使用的 HTTP 代理 |
| `warn_unsupported_recall` | 无法自动撤回 SGWCMAID 消息时是否提示用户手动撤回 |

### 管理命令

| 指令 | 权限 | 说明 |
|---|---|---|
| `开启舞萌功能` | 超级管理员 | 在当前群启用插件功能 |
| `关闭舞萌功能` | 超级管理员 | 在当前群禁用插件功能 |
| `更新maimai数据` | 超级管理员 | 刷新曲库与谱面统计缓存 |
| `更新别名库` | 超级管理员 | 手动刷新别名库 |
| `更新定数表` | 超级管理员 | 生成或更新 `static/mai/rating/` 下的定数表图片 |
| `更新完成表` | 超级管理员 | 生成或更新 `static/mai/plate/` 下的完成表图片 |

### WebUI 管理

WebUI 包含总览、锐评人格、谱面标签、配置管理和命令说明。

锐评人格支持：

- 人格名称
- 聊天样本
- 品味说明
- 特殊说明
- JSON 聊天记录导入

谱面标签支持：

- 生成基础标签文件
- 启动自动更新任务
- 停止任务
- 查看进度、错误和文件路径

谱面标签只处理 Expert、Master、Re:Master，标签白名单为：

```text
交互、纵连、叠键、拍划、错位、拆弹、一笔画、扫键、骗手、耐力、爆发、死镰、定位
```

其中 `短纵` 会归一为 `叠键`。

### 运行数据文件

以下文件是插件运行期会读写的数据。真实部署时请注意隐私和备份。

| 路径 | 内容 |
|---|---|
| `static/config.json` | 旧式静态配置模板 |
| `static/music_data.json` | 曲库缓存 |
| `static/music_chart.json` | 谱面统计缓存 |
| `static/music_alias.json` | 别名缓存 |
| `static/user_import_tokens.json` | 用户水鱼 Import-Token 绑定 |
| `static/arcade_credentials.json` | 用户机台凭据缓存 |
| `static/roast_personas.json` | 锐评人格和样本 |
| `static/webui_config_overrides.json` | WebUI 保存的配置覆盖 |
| `static/disabled_groups.json` | 禁用插件的群列表 |
| `static/group_guess_switch.json` | 群猜歌开关 |
| `static/maimaidx_chart_tags.json` | 谱面标签数据 |
| `static/maimaidx_chart_tags_job.json` | 谱面标签任务状态 |

发布纯净仓库时，建议不要提交完整 `static/mai/` 资源包，也不要提交真实用户 Token、机台凭据或私有人格样本。

## 用户使用说明

### 基础功能

| 指令 | 说明 |
|---|---|
| `帮助` / `help` | 查看帮助图 |
| `今日舞萌` / `今日运势` / `jrys` | 查看今日运势和推荐歌曲 |
| `项目地址maimaiDX` | 查看项目地址 |
| `来个<难度>` | 随机一首指定等级或难度的歌曲，例如 `来个13+` |
| `mai什么` | 随机推荐一首歌；包含推分语义时会尝试结合 B50 推荐 |

### 查歌

| 指令 | 说明 |
|---|---|
| `查歌 <关键词>` / `search <关键词>` | 按标题或别名搜索歌曲 |
| `id <歌曲ID>` | 按 ID 查询歌曲详情 |
| `定数查歌 <定数>` | 按定数查询歌曲 |
| `定数查歌 <下限> <上限> [页数]` | 按定数范围查询歌曲 |
| `bpm查歌 <BPM>` | 按 BPM 查询歌曲 |
| `bpm查歌 <下限> <上限> [页数]` | 按 BPM 范围查询歌曲 |
| `曲师查歌 <曲师名> [页数]` | 按曲师查询歌曲 |
| `谱师查歌 <谱师名> [页数]` | 按谱师查询歌曲 |

`查歌`、`info` / `minfo`、`ginfo` 均支持使用别名匹配曲目。

### 成绩查询

| 指令 | 说明 |
|---|---|
| `b50` | 查询自己的 Best 50 |
| `b50 <水鱼用户名>` | 按水鱼用户名查询 B50 |
| `b50 @用户` | 查询被 @ 用户的 B50 |
| `info <曲名或ID>` / `minfo <曲名或ID>` | 查询自己的单曲成绩详情 |
| `ginfo <曲名或ID>` | 查询歌曲全局统计，默认 Master |
| `ginfo <绿黄红紫白><曲名或ID>` | 查询指定难度全局统计 |
| `查看排名` / `查看排行` | 查看水鱼公开 Rating 排名 |
| `我的排名` | 查看自己在公开 Rating 排名中的位置 |
| `分数线 <难度+歌曲ID> <目标达成率>` | 计算达成率容错 |
| `<定数>的<达成率>是多少分` | 计算 Rating，例如 `14.2的100.5是多少分` |

### 表格和进度

| 指令 | 说明 |
|---|---|
| `<等级>定数表` | 查看等级定数表，例如 `13+定数表` |
| `<等级>完成表` | 查看等级完成表，例如 `13+完成表` |
| `<牌子>完成表` | 查看牌子完成表，例如 `祭将完成表` |
| `<牌子>进度` | 查询自己的牌子进度，例如 `祭将进度` |
| `<等级><评价>进度` | 查询等级评价进度，例如 `13+sss进度` |
| `<定数>分数列表` | 查看指定定数或等级的成绩列表 |
| `我要在<等级>上<分数>分` | 查找可提升 Rating 的谱面 |

### 水鱼绑定与成绩同步

用户个人成绩同步需要先绑定水鱼 Import-Token：

```text
绑定水鱼 <水鱼token>
查看水鱼
解绑水鱼
```

Import-Token 获取位置：

```text
水鱼查分器 -> 编辑个人资料 -> 成绩上传 token
```

首次同步需要发送官方公众号提供的 SGWCMAID：

```text
更新b50 <SGWCMAID识别码>
导 <SGWCMAID识别码>
```

同步成功后，插件会保存机台用户信息。之后可以直接执行：

```text
更新b50
导
```

SGWCMAID 是短时效一次性识别码。插件会尝试撤回含 SGWCMAID 的消息；若撤回失败，请用户手动撤回。

### 锐评与推荐

| 指令 | 说明 |
|---|---|
| `锐评b50` | 生成自己的 B50 锐评图 |
| `锐评b50 <人格名或补充要求>` | 使用指定人格或补充要求生成锐评 |
| `/吃分推荐` | 基于自己的 B50 推荐一首可吃分谱面 |
| `/吃分推荐 @用户` | 为被 @ 用户生成吃分推荐 |

锐评会综合当前 Rating 分段、B35/B15 结构、达成率、实际定数、拟合定数、曲师、谱师、谱面标签和人格样本。

### 猜歌

| 指令 | 说明 |
|---|---|
| `猜歌` | 开始文字提示猜歌 |
| `猜曲绘` | 开始曲绘猜歌 |
| `重置猜歌` | 重置当前群正在进行的猜歌 |
| `开启mai猜歌` | 开启当前群猜歌功能 |
| `关闭mai猜歌` | 关闭当前群猜歌功能 |

猜歌功能仅在群聊中可用。

## 常见问题

### 帮助命令没有图片

检查文件是否存在：

```text
static/help.png
```

### B50 或表格图片生成失败

优先检查：

```text
static/mai/
static/mai/pic/
static/mai/cover/
static/mai/rating/
static/mai/plate/
```

如果资源存在但仍失败，请检查依赖和 Playwright Chromium 是否安装完整。

### 别名查歌不准或没有结果

由超级管理员执行：

```text
更新别名库
```

如果网络异常，插件会尝试使用 `static/music_alias.json` 中的缓存。

### 谱面标签没有生效

检查以下文件是否存在，并在 WebUI 中刷新状态：

```text
static/maimaidx_chart_tags.json
static/maimaidx_chart_tags_job.json
```

吃分推荐和锐评只会读取这些 `static/` 下的标签数据。

## 免责声明

本插件仅用于舞萌 DX 相关数据查询、成绩展示、玩家交流和 Bot 管理辅助，不隶属于 SEGA、舞萌 DX 官方、水鱼查分器、AstrBot 官方或任何第三方数据服务。

插件涉及的曲目信息、成绩数据、曲绘素材、别名数据、谱面标签、搜索结果和第三方接口响应，其版权、商标权、数据权利和解释权归原权利方所有。

使用、部署、修改或分发本插件时，请遵守游戏官方、平台、社区和第三方数据服务的使用规则。开发者不对账号风险、数据丢失、接口限制、服务不可用、部署错误或其他直接及间接损失承担责任。

请勿将本插件用于商业用途、违规爬取、恶意请求、绕过平台限制、泄露他人隐私或侵犯任何第三方权益的场景。
