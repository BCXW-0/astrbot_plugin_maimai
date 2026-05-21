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

`astrbot_plugin_maimaidx` 是面向 AstrBot 的舞萌 DX 插件，提供歌曲查询、成绩查询、B50 图片、B50 锐评、成绩同步、谱面标签、吃分推荐、猜歌和插件 WebUI 等能力。

本文所有目录均以插件根目录 `astrbot_plugin_maimaidx/` 为基准。例如 `static/help.png` 表示插件目录下的 `static/help.png`，不是系统绝对路径。

当前版本相对上游主要调整：

- 保留查歌、查分、B50、定数表、完成表、猜歌等基础能力。
- 移除别名提交、别名投票、别名推送和机厅排卡相关功能。
- 恢复 Bot 超级管理员命令 `更新别名库`。
- 增加用户水鱼 Import-Token 绑定、SGWCMAID 成绩同步、锐评人格 WebUI、谱面标签和吃分推荐。
- `1.3.0` 修复纯净环境按 `requirements.txt` 安装后可能缺少定时任务依赖，导致插件导入失败的问题。
- `1.2.2` 优化吃分推荐推送逻辑：合并推荐理由和曲目信息图，补充 B50 倾向标签，并从排序后的前最多 10 个候选谱面中随机推荐。
- 曲目标签 JSON 固定保存在 `static/maimaidx_chart_tags.json`，任务状态固定保存在 `static/maimaidx_chart_tags_job.json`。
- 纯净仓库不包含完整 `static/mai/` 静态资源包。

---

## 准备工作

本章节面向 Bot 管理员。建议按顺序完成依赖、配置、静态资源和初始化命令后，再重载插件或重启 AstrBot。

### 1. 安装依赖

如果 AstrBot 没有自动安装插件依赖，请在插件目录执行：

```bash
pip install -r requirements.txt
```

如果生成 B50、表格或图表时报 Playwright / Chromium 相关错误，再执行：

```bash
python -m playwright install chromium
```

### 2. 配置 Bot 超级管理员

管理类命令依赖 AstrBot 主配置中的 `admins_id`。请在 AstrBot 主配置中添加管理员 QQ 号，而不是写入插件配置文件。

以下命令需要 Bot 超级管理员权限：

```text
更新maimai数据
更新别名库
更新定数表
更新完成表
开启舞萌功能
关闭舞萌功能
```

其中首次部署或资源更新后，建议至少执行这四个初始化命令：

```text
更新maimai数据
更新别名库
更新定数表
更新完成表
```

| 指令 | 用途 |
|---|---|
| `更新maimai数据` | 获取/刷新曲库与谱面统计缓存，包括拟合定数等数据 |
| `更新别名库` | 获取/刷新查歌使用的曲目别名缓存 |
| `更新定数表` | 生成或更新等级定数表图片 |
| `更新完成表` | 生成或更新牌子完成表图片 |

### 3. 配置插件基础项

在 AstrBot 插件配置界面中配置本插件。重要配置项如下：

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `bot_name` | 空 | 机器人显示名称；留空则不强制写入名称 |
| `enable_reply` | `true` | 是否在多数回复中附带引用消息 |
| `maimaidxtoken` | 空 | 水鱼 Developer-Token，用于开发者接口查询 |
| `roast_b50_provider_id` | 空 | 锐评 B50 专用模型 Provider ID；留空则使用当前默认模型 |
| `roast_persona_prompt_sample_limit` | `120` | 每次锐评注入的人格样本上限 |
| `roast_persona_webui_enabled` | `false` | 是否启用插件 WebUI |
| `roast_persona_webui_host` | `127.0.0.1` | WebUI 监听地址 |
| `roast_persona_webui_port` | `8796` | WebUI 监听端口 |
| `roast_persona_webui_token` | 空 | WebUI 访问 Token；监听非本机地址时必填 |
| `sgid_max_age_seconds` | `180` | SGWCMAID 有效窗口，单位秒 |
| `request_timeout_seconds` | `30` | 成绩同步网络请求超时时间 |
| `maimai_http_proxy` | 空 | 成绩同步访问官方数据源时使用的 HTTP 代理 |
| `warn_unsupported_recall` | `true` | 无法自动撤回 SGWCMAID 消息时是否提醒用户 |

`maimaidxtoken` 是水鱼 Developer-Token，不是用户个人 Import-Token。用户个人 Import-Token 由用户在群聊中通过 `绑定水鱼 <水鱼token>` 自行绑定。

请不要把真实 Token、用户凭据或私有人格样本提交到仓库。

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

当前插件不提供群聊内更新完整静态资源包的命令。需要更新曲绘、Rating 素材或牌子素材时，请重新下载资源包并覆盖 `static` 目录。

### 5. 初始化曲库、别名和表格资源

完成静态资源部署后，由 Bot 超级管理员执行：

```text
更新maimai数据
更新别名库
更新定数表
更新完成表
```

这些命令会生成或刷新运行期缓存，例如：

```text
static/music_data.json
static/music_chart.json
static/music_alias.json
static/mai/rating/
static/mai/plate/
```

这些内容可以通过命令重新获取或生成，纯净仓库通常不需要提交。

### 6. 初始化谱面标签

锐评 B50 和吃分推荐会读取谱面标签。首次部署后，建议在插件 WebUI 的「谱面标签」页执行：

```text
生成基础标签文件
自动更新补缺
刷新状态
```

谱面标签相关文件固定保存在：

```text
static/maimaidx_chart_tags.json
static/maimaidx_chart_tags_job.json
```

请注意：本地代码和仓库文档均使用 `static/` 作为曲目标签 JSON 的保存目录，不使用其他运行时目录。

谱面标签库只收录定数不低于 `12.6` 的 Expert、Master、Re:Master 谱面；定数 `12.0` 至 `12.5` 以及更低定数的谱面不会进入标签维护和自动更新范围。标签白名单为：

```text
节奏、背谱、管子、定位、散打、手序、飞手、防蹭、留尾、爆发、底力、交互、一笔划、双押、扫键、死镰、错位、手速、纵连、子弹、跳拍、延迟星星、如龙、秒划、拆谱
```

其中「节奏」指节奏比较怪异；「管子」特指“管子海”配置；「定位」指键盘手位或按区定位；「飞手」指大位移；「防蹭」指星星防蹭，也就是星星定位相关的防误触需求；「底力」通常综合爆发和高物量，「硬抗」会归入「底力」；「双押」特指双押海或大位移双押；「扫键」指扫键、扫圈、转圈类配置；「如龙」指如龙扫配置；「秒划」包含秒划星星；「拍划」不作为独立标签，会归入「错位」；「骗手」会归入「手序」；「手速」指高 BPM（通常 240 以上）或高键密度，和局部「爆发」区分；「纵连」指长纵，「子弹」指 2、3 个键组成的短纵。

已维护的谱面标签会随插件静态资源一起发布到 `static/maimaidx_chart_tags.json`。普通管理员更新插件后，本地标签文件会作为自动更新底座；随后执行自动更新时，只会补齐仍缺失或规则过期的谱面，因此联网和计算成本会明显低于从空库开始全量分析。

自动更新只基于 Bilibili、Gamerch maimai 攻略页和 YouTube 等玩家攻略或谱面确认资料，通过保守关键词规则抽取白名单主观标签；没有可用资料或没有命中白名单标签时，会标记为无资料，不会调用 LLM，也不会根据本地谱面信息自动猜标签。已经存在手动标签、最终标签、旧标签或自动标签的谱面不会加入自动队列，重新生成基础标签文件时也会保留已有标签、证据和状态。

自动更新仍按批次处理并写入进度，但批次之间会连续执行，不再等待固定间隔；需要暂停时可在 WebUI 点击「停止任务」。

### 7. 准备帮助图

`帮助` / `help` 命令会发送：

```text
static/help.png
```

建议管理员根据 Bot 名称、群规、WebUI 地址和常用命令自行设计并替换这张图。

### 8. 启用插件 WebUI

如需使用锐评人格、谱面标签和配置管理，请在插件配置中启用 WebUI：

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

如果 WebUI 监听 `0.0.0.0`、公网 IP 或其他非本机地址，必须配置 `roast_persona_webui_token`，否则插件会拒绝启动 WebUI。未配置 Token 时仅建议保持默认 `127.0.0.1` 本机访问。访问根路径未携带 Token 时会显示 Token 输入页；API 接口仍需要通过 `?token=你的token` 或 `X-Access-Token` 请求头校验。

---

## 面向 Bot 管理员的使用说明

### 管理命令

| 指令 | 权限 | 说明 |
|---|---|---|
| `开启舞萌功能` | 超级管理员 | 在当前群启用插件功能 |
| `关闭舞萌功能` | 超级管理员 | 在当前群禁用插件功能 |
| `更新maimai数据` | 超级管理员 | 刷新曲库与谱面统计缓存 |
| `更新别名库` | 超级管理员 | 刷新曲目别名缓存 |
| `更新定数表` | 超级管理员 | 生成或更新 `static/mai/rating/` 下的定数表图片 |
| `更新完成表` | 超级管理员 | 生成或更新 `static/mai/plate/` 下的完成表图片 |

群开关只影响当前群。插件关闭后，普通用户命令不会继续响应；超级管理员仍可再次执行 `开启舞萌功能`。

### WebUI 管理

插件 WebUI 包含：

- 总览
- 锐评人格
- 谱面标签
- 命令说明
- 配置管理

锐评人格支持配置：

- 人格名称
- 聊天样本
- 品味说明
- 特殊说明
- JSON 聊天记录导入

导入聊天记录时会过滤图片、at、引用、表情、语音、视频、文件等非纯文本内容。

谱面标签页面支持：

- 生成基础标签文件
- 启动自动更新补缺任务
- 停止任务
- 查看当前进度、错误和文件路径
- 搜索谱面并查看当前标签、自动抽取证据和谱面基础信息
- 为单个谱面保存或清空手动标签

### 运行期文件说明

以下文件或目录会在运行期读写。发布纯净仓库时，请谨慎处理其中的隐私数据和可再生成资源。

| 路径 | 内容 | 是否建议提交到纯净仓库 |
|---|---|---|
| `static/config.json` | 旧式静态配置模板，Token 应保持为空 | 可提交空模板 |
| `static/music_data.json` | 曲库缓存 | 不建议 |
| `static/music_chart.json` | 谱面统计缓存 | 不建议 |
| `static/music_alias.json` | 别名缓存 | 不建议 |
| `static/local_music_alias.json` | 本地自定义别名 | 按需，注意隐私与维护成本 |
| `static/user_import_tokens.json` | 用户水鱼 Import-Token | 不应提交 |
| `static/arcade_credentials.json` | 用户机台凭据 | 不应提交 |
| `static/roast_personas.json` | 锐评人格和样本 | 不应提交私有样本 |
| `static/webui_config_overrides.json` | WebUI 保存的配置覆盖 | 不应提交真实部署配置 |
| `static/disabled_groups.json` | 群禁用状态 | 不应提交 |
| `static/group_guess_switch.json` | 群猜歌开关 | 不应提交 |
| `static/maimaidx_chart_tags.json` | 谱面标签数据 | 可提交空模板或维护后的公共标签库 |
| `static/maimaidx_chart_tags_job.json` | 谱面标签任务状态 | 可提交空模板 |
| `static/mai/` | 曲绘、牌子、Rating 和绘图素材 | 不建议提交完整资源包 |

### 数据与安全建议

- `maimaidxtoken` 请通过 AstrBot 插件配置或 WebUI 配置，不要写死在代码中。
- 用户个人 Import-Token 和机台凭据属于敏感数据，不要上传到公开仓库。
- WebUI 如果监听 `0.0.0.0`、公网 IP 或其他非本机地址，必须设置访问 Token；未配置 Token 时只允许本机访问。
- SGWCMAID 是短时效识别码，插件会尝试撤回包含 SGWCMAID 的消息；如果撤回失败，请提醒用户手动撤回。

---

## 面向用户的使用说明

### 基础功能

| 指令 | 说明 |
|---|---|
| `帮助` / `help` | 查看帮助图 |
| `今日舞萌` / `今日运势` / `jrys` | 查看今日运势和推荐歌曲 |
| `来个<难度>` | 随机一首指定等级或难度的歌曲，例如 `来个13+` |
| `mai什么` | 随机推荐一首歌；包含推分语义时会尝试结合 B50 推荐 |

### 歌曲查询

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
| `/吃分推荐` | 按自己的当前 Rating 区间、B35/B15 地板和拟合定数推荐吃分谱面 |
| `/吃分推荐 @用户` | 为被 @ 用户生成吃分推荐 |

吃分推荐会先从水鱼查分器读取当前 B50 和 Rating，再按以下顺序筛选：基础定数范围为 `当前 Rating / 1130` 到 `当前 Rating / 1075`，下限不低于对应 B35/B15 地板换算出的 SSS+ 所需定数，上限不超过 `15.0`；SSS+ 理论单曲 Rating 高于对应 B35/B15 的已有最低正分；谱面未以 SSS+ 出现在 B50 中；最后优先推荐实际定数更低的谱面，实际定数相同时再推荐“拟合定数 - 实际定数”更低的谱面。插件会从排序后的前最多 10 个候选谱面中随机推荐一首；候选不足 10 首时只使用实际候选数量，没有候选时直接返回无候选提示。推荐理由、B50 倾向标签和曲目信息图会合并为一条引用回复。

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

---

## 常见问题

### 帮助命令没有图片

检查文件是否存在：

```text
static/help.png
```

### B50 或表格图片生成失败

优先检查资源目录是否完整：

```text
static/mai/
static/mai/pic/
static/mai/cover/
static/mai/rating/
static/mai/plate/
```

如果资源存在但仍失败，请检查依赖和 Playwright Chromium 是否安装完整。

### 别名查歌不准或没有结果

由 Bot 超级管理员执行：

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

吃分推荐和锐评只会读取 `static/` 下的标签数据。

如果自动更新后谱面仍显示无资料，表示当前没有找到足够的中文玩家社区证据或没有抽取到白名单标签；管理员可以在 WebUI 的「谱面标签」页搜索该谱面并手动维护标签。

---

## 免责声明

本插件仅用于舞萌 DX 相关数据查询、成绩展示、玩家交流和 Bot 管理辅助，不隶属于 SEGA、舞萌 DX 官方、水鱼查分器、AstrBot 官方或任何第三方数据服务。

插件涉及的曲目信息、成绩数据、曲绘素材、别名数据、谱面标签、搜索结果和第三方接口响应，其版权、商标权、数据权利和解释权归原权利方所有。

使用、部署、修改或分发本插件时，请遵守游戏官方、平台、社区和第三方数据服务的使用规则。开发者不对账号风险、数据丢失、接口限制、服务不可用、部署错误或其他直接及间接损失承担责任。

请勿将本插件用于商业用途、违规爬取、恶意请求、绕过平台限制、泄露他人隐私或侵犯任何第三方权益的场景。
