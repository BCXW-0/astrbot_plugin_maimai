# 🌸 astrbot_plugin_maimai · 舞萌 DX 插件

> 一个面向 AstrBot 的舞萌 DX 插件：查歌、查分、B50、锐评 B50、成绩同步、猜歌、谱面标签与吃分推荐。

---

## ✨ 功能概览

| 模块 | 能力 |
|---|---|
| 🎵 歌曲查询 | 查歌、按 ID 查询、按定数/BPM/曲师/谱师查询 |
| 📊 成绩查询 | B50、单曲成绩、全局成绩信息、排行榜 |
| 🔥 锐评 B50 | 结合 B35/B15、定数、拟合定数、谱面标签和人格风格生成锐评 |
| 🚀 成绩同步 | 支持 SGWCMAID 首次绑定，后续复用机台用户信息更新 B50 |
| 🧠 谱面标签 | WebUI 批量搜索资料并用 LLM 抽取谱面标签，支持断点续跑 |
| 🍚 吃分推荐 | 根据玩家 B50 擅长标签、B35/B15 最低分、拟合定数和实际定数推荐吃分曲 |
| 🎮 猜歌 | 曲绘猜歌与答案判定 |
| 🧩 插件 WebUI | 人格管理、谱面标签更新、命令说明、配置摘要 |

已移除：别名提交/投票、别名推送、机厅排卡相关功能。

---

## 🧰 前期准备

### 1. 准备水鱼 Developer-Token

插件级配置项：

```text
maimaidxtoken
```

用于访问水鱼开发者接口，例如 B50、牌子、单曲成绩等。它不是用户个人 Import-Token。

### 2. 用户绑定 Import-Token

用户个人成绩上传需要自己绑定水鱼 Import-Token：

```text
绑定水鱼 <水鱼token>
查看水鱼
解绑水鱼
```

水鱼 token 获取位置：

```text
水鱼查分器 → 编辑个人资料 → 成绩上传 token
```

### 3. 准备静态资源

曲绘、牌子、Rating 等静态资源体积较大，仓库中不直接包含完整资源包。首次部署或资源缺失时，请下载：

```text
https://cloud.yuzuchan.moe/f/nXt6/Resource.7z
```

下载后将压缩包内容解压并覆盖插件的 `static` 目录，例如：

```bash
7z x Resource.7z -y -o/path/to/astrbot_plugin_maimai/static
```

覆盖后应能看到 `static/mai` 等资源目录。

如果后续需要更新静态资源，也按同样方式重新下载 `Resource.7z` 并解压覆盖 `static` 目录；当前插件未提供群聊内「更新静态资源」命令，静态资源请在部署环境中手动更新。

### 4. 初始化谱面标签

锐评 B50 和吃分推荐会用到谱面标签。首次部署后，建议在插件管理 WebUI 的「谱面标签」页执行：

```text
生成基础标签文件
启动自动更新
刷新状态
```

谱面标签数据会保存到：

```text
/root/astrbot_runtime/TAGS/maimaidx_chart_tags.json
```

更新过程支持断点续跑；如果不需要锐评和吃分推荐中的谱面标签能力，可以暂时跳过。

### 5. 准备帮助图

`帮助` / `help` 命令会发送图片：

```text
/root/astrbot_runtime/data/plugins/astrbot_plugin_maimai/static/help.png
```

建议根据你的 Bot 名称、群规、常用命令、WebUI 地址和实际运营信息，自定义一张 `help.png` 放到插件的 `static` 目录下。

### 6. 启用插件管理 WebUI

在 AstrBot 插件配置界面中配置：

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

---

## 📌 常用命令

### 基础

```text
帮助 / help
今日舞萌 / 今日运势 / jrys
开启舞萌功能
关闭舞萌功能
更新maimai数据
项目地址maimaiDX
```

### 查歌

```text
查歌 <关键词>
search <关键词>
id <歌曲ID>
定数查歌 <定数>
bpm查歌 <BPM>
曲师查歌 <曲师名>
谱师查歌 <谱师名>
```

### 成绩

```text
b50 [QQ号或@用户]
info <曲名或ID>
minfo <曲名或ID>
ginfo <曲名或ID>
分数线 <难度+歌曲ID> <目标达成率>
<定数>的<达成率>是多少分
查看排名
我的排名
```

### 锐评与推荐

```text
锐评b50
锐评b50 <人格名或补充要求>
/吃分推荐
/吃分推荐 @某人
```

`/吃分推荐` 会：

1. 获取玩家 B50。
2. 区分 B35 和 B15 的最低 Rating。
3. 分析玩家 B50 中高频命中的谱面标签。
4. 在谱面标签库中查找候选曲目。
5. 结合拟合定数、实际定数、是否新曲、标签命中情况，推荐一首理论可吃分曲。
6. 像 `查歌` 一样渲染对应曲目信息，并额外输出推荐理由。

### 成绩同步

```text
绑定水鱼 <水鱼token>
解绑水鱼
查看水鱼
更新b50 <SGWCMAID识别码>
更新b50
导 <SGWCMAID识别码>
导
```

说明：

- 首次同步需要发送 SGWCMAID。
- 成功后会保存由 SGWCMAID 解析出的机台用户信息。
- 之后可以直接发送 `更新b50` / `导` 复用机台用户信息。
- SGWCMAID 本身是短时效一次性识别码，不会被长期复用。

### 猜歌

```text
猜歌
猜曲绘
重置猜歌
开启mai猜歌
关闭mai猜歌
```

---

## 🔥 锐评 B50

`锐评b50` 会综合：

- 当前 Rating 分段
- B35 / B15 结构
- 达成率和 song_rating
- 实际定数与拟合定数
- 曲师、谱师、谱面类型
- 谱面标签
- 人格样本、品味说明、特殊说明

谱面标签只会以极简形式注入到 B50 上下文中，例如：

```text
标签:交互/爆发/定位
```

不会注入完整 JSON 或搜索证据，避免不必要的 token 消耗。

---

## 🧠 谱面标签

谱面标签文件保存到：

```text
/root/astrbot_runtime/TAGS/maimaidx_chart_tags.json
```

任务状态保存到：

```text
/root/astrbot_runtime/TAGS/maimaidx_chart_tags_job.json
```

标签白名单：

```text
交互、纵连、叠键、拍划、错位、拆弹、一笔画、扫键、骗手、耐力、爆发、死镰、定位
```

`短纵` 会归一为 `叠键`。

谱面标签不通过群聊命令更新，只能在 WebUI 的「谱面标签」页操作：

```text
生成基础标签文件
启动自动更新
停止任务
刷新状态
```

自动更新规则：

- 按水鱼曲目清单的谱面 ID 升序处理。
- 只处理 Expert / Master / Re:Master。
- 每批最多 50 个谱面。
- 每个谱面失败后自动重试 1 次。
- 批次之间会等待一段时间，降低被搜索网站限制的概率。
- 每处理完一个谱面都会写回文件，支持断点续跑。

---

## 🧩 插件管理 WebUI

WebUI 包含：

- 总览
- 锐评人格
- 谱面标签
- 命令说明
- 配置管理

### 锐评人格

支持配置：

- 人格名称
- 聊天样本
- 品味说明
- 特殊说明

人格样本可以长期积累，不设总量上限；每次注入 LLM 的样本数量由：

```text
roast_persona_prompt_sample_limit
```

控制，默认 120。

JSON 聊天记录导入会过滤图片、at、引用、表情、语音、视频、文件等非纯文本内容。

---

## ⚙️ 关键配置

| 配置项 | 说明 |
|---|---|
| `maimaidxtoken` | 水鱼 Developer-Token |
| `roast_b50_provider_id` | 锐评 B50 专用模型 Provider ID |
| `roast_persona_prompt_sample_limit` | 锐评人格样本注入上限 |
| `roast_persona_webui_enabled` | 是否启用插件管理 WebUI |
| `roast_persona_webui_host` | WebUI 监听地址 |
| `roast_persona_webui_port` | WebUI 端口 |
| `roast_persona_webui_token` | WebUI 访问 token |
| `sgid_max_age_seconds` | SGID 有效窗口 |
| `request_timeout_seconds` | 成绩同步请求超时 |
| `maimai_http_proxy` | 成绩同步 HTTP 代理 |
| `warn_unsupported_recall` | 无法自动撤回敏感消息时是否提示 |

---

## 📦 上传 GitHub 建议

建议上传代码与必要资源，但排除：

```text
static/mai/
```

以及运行时可更新、可生成或含用户数据的内容，例如：

```text
__pycache__/
*.pyc
/root/astrbot_runtime/TAGS/
用户 token / 机台凭据 / 本地人格库等运行时数据
```

插件基本信息以 `metadata.yaml` 为准。

---

## ✅ 注意事项

1. 修改插件代码、WebUI 静态资源或配置 schema 后，请重启 AstrBot 或重载插件。
2. 修改 WebUI 后建议浏览器强制刷新，避免缓存旧版前端资源。
3. 如果 WebUI 暴露到公网，务必配置访问 token。
4. `帮助 / help` 始终优先发送 `static/help.png`，只有图片缺失时才提示管理员。
5. 谱面标签质量取决于搜索资料与 LLM 判断，建议定期复查热门谱面的标签。
