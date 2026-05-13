---
name: "xhs-single-note-collect"
description: "采集小红书单条笔记的全链路数据（正文、媒体、互动、博主），回写到飞书多维表格。Use when: 需要从飞书读取笔记链接 → 采集小红书数据 → 回写飞书，中间处理附件上传。"
---

# 小红书单条笔记采集

> 一条命令链完成「读飞书 → 采小红书 → 回写飞书」闭环，自动处理媒体下载、附件上传、去重和速率限制。

## ⏱ 耗时预估

脚本含 6 个阶段，耗时大头在 API 速率限制。以下是 **N 条不重复笔记** 的参考耗时：

| 阶段 | 单条耗时 | N 条合计 | 说明 |
|------|----------|----------|------|
| `get_note_info` × N | ~110-130s | ~110-130N s | scrape 限速 |
| `get_user_info` × N | ~1-2s | ~1-2N s | light 限速 |
| `get_note_all_comment` × N | ~110-130s | ~110-130N s | scrape 限速 |
| 媒体下载 + 附件上传 | ~5-10s | ~5-10N s | 网络速度决定 |
| 回写字段 | ~2-3s | ~2-3N s | 飞书 API |

**典型场景**（以 N=3 不重复笔记为例）：
- scrape 类：6 次（`get_note_info` ×3 + `get_note_all_comment` ×3），每次 110-130s → **660-780s（11-13min）**
- light 类：3 次（`get_user_info` ×3），每次 0.5-1.5s → **~3s**
- 媒体 + 回写：~30s
- **合计约 12-14 分钟**

去重后若 N 减少，耗时等比例降低。交互模式会在开始前打印预估总时间。

> **建议超时设置**：脚本本身不超时，调用方(终端/CI)设为 `--timeout 1800000`(30min) 以上，N=5 时需 ≥ 40min。

---

## 快速开始（推荐）

### 方式 A：交互模式（推荐新手）

直接运行脚本，不传参即可进入交互向导：

```powershell
# 检查 Python 依赖（已安装则跳过）
pip list --format=columns 2>$null | Select-String "requests|loguru" >$null 2>$null
if ($LASTEXITCODE -ne 0) { pip install -r skills/xhs-apis/scripts/requirements.txt }

# 检查 Node 依赖（已安装则跳过）
if (-not (Test-Path skills/xhs-apis/scripts/node_modules)) {
    Push-Location skills/xhs-apis/scripts; npm install; Pop-Location
}

lark-cli auth login --domain base

python skills/xhs-single-note-collect/scripts/xhs_collect.py
```

交互向导会依次要求输入：
1. 小红书 cookies（粘贴字符串或指定文件）
2. 飞书表格链接（如 `https://my.feishu.cn/base/TOKEN?table=TBL&view=VIEW`）
3. 行号（逗号分隔，如 `26,27,28`）

确认后自动执行全部 6 步。

### 方式 B：命令行传参（适合重复运行）

```powershell
python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --base-token <BASE_TOKEN> --table-id <TABLE_ID> --view-id <VIEW_ID> `
  --rows 26,27,28 `
  --cookies-str "<COOKIES>"
```

或使用 `--feishu-url` 替代三个独立参数：

```powershell
python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --feishu-url "https://my.feishu.cn/base/TMsGbgncba7qdMsHpxfcO8dSnNf?table=tbltm1ucPvxmwHkA&view=vew5J9vzNJ" `
  --rows 28,29,30 `
  --cookies-str "<COOKIES>"
```

脚本会自动处理：
- 按 view 顺序解析行号 → record_id
- 跨行去重（同一条笔记多行只采一次，节省速率等待）
- get_note_info / get_user_info / get_note_all_comment
- 媒体下载 + 飞书附件上传
- 全部字段回写

### 脚本参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--base-token` | 按需 | 飞书多维表格 Base Token |
| `--table-id` | 按需 | 表 ID |
| `--view-id` | 按需 | 视图 ID（决定行号顺序） |
| `--feishu-url` | 否 | 飞书表格链接，替代 `--base-token --table-id --view-id` |
| `--rows` | 按需 | 行号，逗号分隔，如 `26,27,28` |
| `--record-ids` | 否 | 替代 rows：直接指定 record_id |
| `--cookies-str` | 按需 | 小红书 cookies 字符串 |
| `--cookies-file` | 否 | cookies 文本文件路径 |
| `--skip-comments` | 否 | 跳过评论采集（更快） |
| `--skip-media` | 否 | 跳过媒体下载上传（仅写文本） |
| `--use-cache-only` | 否 | 仅使用缓存数据，不调用 XHS API |
| `--writeback-only` | 否 | 跳过全部采集，仅回写已有缓存数据 |
| `--skip-note-info` | 否 | 跳过 get_note_info（使用缓存） |
| `--skip-user-info` | 否 | 跳过 get_user_info（使用缓存） |

> 所有参数均为可选：不传参时自动进入交互模式逐个询问。

### 使用 cookies 文件

```powershell
# 保存 cookies 到文件
@"abRequestId=...;a1=...;web_session=..."@ | Set-Content -Encoding UTF8 cookies.txt

python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --base-token <TOKEN> --table-id <TBL> --view-id <VIEW> `
  --rows 26,27,28 `
  --cookies-file cookies.txt
```

### 仅回写模式（已有缓存时跳过采集）

```powershell
python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --base-token <TOKEN> --table-id <TBL> --view-id <VIEW> `
  --rows 26,27,28 `
  --cookies-file cookies.txt `
  --use-cache-only
```

或者更细粒度控制：

```powershell
# 仅回写已有缓存，不碰采集链路
python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --base-token <TOKEN> --table-id <TBL> --view-id <VIEW> `
  --rows 26,27,28 `
  --cookies-file cookies.txt `
  --writeback-only

# 只跳过笔记信息采集（如补用户信息）
python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --base-token <TOKEN> --table-id <TBL> --view-id <VIEW> `
  --rows 26,27,28 `
  --cookies-file cookies.txt `
  --skip-note-info

# 只补笔记信息（跳过用户信息采集）
python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --base-token <TOKEN> --table-id <TBL> --view-id <VIEW> `
  --rows 26,27,28 `
  --cookies-file cookies.txt `
  --skip-user-info
```

> **注意：** PowerShell 中跨行命令使用反引号 `` ` ``，不是 `^`（`^` 是 cmd.exe 的续行符）。

---

## 手工工作流（备选）

当脚本不可用或需要调试时，按以下 6 步操作。

### 前置条件

```powershell
# 检查 Python 依赖（已安装则跳过）
pip list --format=columns 2>$null | Select-String "requests|loguru" >$null 2>$null
if ($LASTEXITCODE -ne 0) { pip install -r skills/xhs-apis/scripts/requirements.txt }

# 检查 Node 依赖（已安装则跳过）
if (-not (Test-Path skills/xhs-apis/scripts/node_modules)) {
    Push-Location skills/xhs-apis/scripts; npm install; Pop-Location
}

lark-cli auth login --domain base
```

### Step 1: 探查字段结构

```powershell
lark-cli base +field-list --base-token <BASE_TOKEN> --table-id <TABLE_ID> --as user
```

如缺少字段（如「正文」）：
```powershell
lark-cli base +field-create --base-token <BASE_TOKEN> --table-id <TABLE_ID> --json '{"name":"正文","type":"text","style":{"type":"plain"}}' --as user
```

### Step 2: 读取笔记链接

先获取记录列表确定 record_id：
```powershell
# 注意：输出可能被截断，用 Out-File 保存到文件再查看
lark-cli base +record-list --base-token <BASE_TOKEN> --table-id <TABLE_ID> --view-id <VIEW_ID> --limit 200 --as user 2>&1 | Out-File -Encoding UTF8 records.txt -Width 9999
```

再取具体行的链接：
```powershell
lark-cli base +record-get --base-token <BASE_TOKEN> --table-id <TABLE_ID> --record-id <RECORD_ID> --as user 2>&1 | Out-File -Encoding UTF8 record.txt -Width 9999
```

### Step 3: 采集笔记信息

```powershell
cd skills/xhs-apis/scripts

# 写 payload（避免 PowerShell 分号转义）
@'
{"url": "<NOTE_URL>", "cookies_str": "<COOKIES>"}
'@ | Set-Content -Encoding UTF8 payload.json

# 调用 API（注意 110-130s 速率限制等待）
python xhs_api_tool.py call pc get_note_info --params-file payload.json --out note_info.json

# 提取数据（Python 避免 GBK 乱码）
python -c "
import json
d=json.load(open('note_info.json','r',encoding='utf-8'))
i=d['result'][2]['data']['items'][0]['note_card']
print('nickname:', i['user']['nickname'])
print('desc:', i['desc'])
print('liked:', i['interact_info']['liked_count'])
print('collected:', i['interact_info']['collected_count'])
print('comments:', i['interact_info']['comment_count'])
print('shares:', i['interact_info']['share_count'])
print('user_id:', i['user']['user_id'])
print('time:', i['time'])
"
```

### Step 4: 采集博主信息

```powershell
python xhs_api_tool.py call pc get_user_info --params '{"user_id":"<USER_ID>","cookies_str":"<COOKIES>"}' --out user_info.json

python -c "
import json
d=json.load(open('user_info.json','r',encoding='utf-8'))
its=d['result'][2]['data']['interactions']
for i in its: print(i['type'], i['count'])
"
```

### Step 5: 采集评论

```powershell
python xhs_api_tool.py call pc get_note_all_comment --params-file payload.json --out comments.json
```

### Step 6: 下载媒体 → 上传 → 回写

```powershell
# 下载（用 curl）
curl -L -o cover.jpg "<COVER_URL>"

# 上传附件
Set-Location <DOWNLOAD_DIR>
lark-cli base +record-upload-attachment --base-token <BASE_TOKEN> --table-id <TABLE_ID> --record-id <RECORD_ID> --field-id "图片附件" --file ./cover.jpg --as user
lark-cli base +record-upload-attachment --base-token <BASE_TOKEN> --table-id <TABLE_ID> --record-id <RECORD_ID> --field-id "正文图" --file ./body.jpg --as user
lark-cli base +record-upload-attachment --base-token <BASE_TOKEN> --table-id <TABLE_ID> --record-id <RECORD_ID> --field-id "视频附件" --file ./video.mp4 --as user

# 回写字段
lark-cli base +record-upsert --base-token <BASE_TOKEN> --table-id <TABLE_ID> --record-id <RECORD_ID> --as user --json '{
  "作者名":"<VALUE>",
  "标题":"<VALUE>",
  "正文":"<VALUE>",
  "标签":"<VALUE>",
  "点赞数":<NUMBER>,
  "评论数":<NUMBER>,
  "分享数":<NUMBER>,
  "收藏数":<NUMBER>,
  "发布时间":"YYYY-MM-DD HH:mm:ss",
  "提取时间":"YYYY-MM-DD HH:mm:ss",
  "粉丝":<NUMBER>,
  "获赞与收藏":<NUMBER>,
  "图片链接":"<URL>",
  "视频链接":"<URL or empty>"
}'
```

---

## 坑点清单

| # | 坑 | 现象 | 规避 |
|---|-----|------|------|
| 1 | PowerShell 分号转义 | cookies 中 `;` 被当成命令分隔符 | 用 `--params-file` 传 JSON 文件，或直接用编排脚本 |
| 2 | xhs-apis 速率限制 | scrape 类(get_note_info / get_note_all_comment)每次 sleep **110-130s**，light 类(get_user_info)仅 0.5-1.5s<br>例：3 条笔记 = 6 次 scrape 调用 → **11-13 分钟纯等待** | 编排脚本自动等待；手工操作时提前规划，见上方⏱耗时预估 |
| 3 | GBK 编码报错 | 评论含 emoji 时 `UnicodeEncodeError` | 永远用 `--out` 写文件；或设 `$env:PYTHONIOENCODING='utf-8'`；或使用编排脚本 |
| 4 | lark-cli `--file` 路径限制 | 不接受绝对路径 | 用相对路径 `./file`，或编排脚本自动处理 |
| 5 | `+record-upsert` JSON 结构 | 误套 `{"field_values":{...}}` 外层 | 直接 `{"字段名": 值}` |
| 6 | 缺少字段 | 表中无「正文」字段 | `+field-list` 先比对，缺失则 `+field-create` |
| 7 | 编码地狱 | ANSI 转义码 + UTF-8 内容 + GBK 终端 | 用 `Out-File -Encoding UTF8` 存文件，用 Python 读取；编排脚本自动处理 |
| 8 | 记录列表截断 | `+record-list` 因单条记录内容过大被截断 | `Out-File -Width 9999`；编排脚本内部处理 |
| 9 | 重复笔记 | 多行指向同一条笔记 | 编排脚本自动按 note_id 去重，节省速率等待时间 |
| 10 | 数据动态变化 | 分步采集期间数据可能更新 | 记录采集时间戳；编排脚本统一采集时间点 |
| 11 | Windows 上 `lark-cli` 不可直接 spawn | Python 子进程 `FileNotFoundError`，因为 lark-cli 是 `.ps1` 脚本，`CreateProcess` 不认 | 脚本内 `_larkcli_cmd()` 已适配：Windows 调用 `pwsh -File lark-cli.ps1`，Unix 直接调 `lark-cli` |


---

## 字段映射参考

| 回写目标 | 字段名 | 类型 | 采集来源 |
|----------|--------|------|----------|
| 作者名 | 作者名 | text | `note_card.user.nickname` |
| 标题 | 标题 | text | `note_card.title` |
| 正文 | 正文 | text | `note_card.desc` |
| 标签 | 标签 | text | `tag_list[].name` (逗号拼接) |
| 点赞数 | 点赞数 | number | `interact_info.liked_count` (去单位) |
| 评论数 | 评论数 | number | `interact_info.comment_count` |
| 分享数 | 分享数 | number | `interact_info.share_count` |
| 收藏数 | 收藏数 | number | `interact_info.collected_count` (去单位) |
| 发布时间 | 发布时间 | datetime | `note_card.time` (ms→`yyyy-MM-dd HH:mm:ss`) |
| 提取时间 | 提取时间 | datetime | 当前时间 |
| 粉丝 | 粉丝 | number | `interactions[type=fans].count` |
| 获赞与收藏 | 获赞与收藏 | number | `interactions[type=interaction].count` |
| 图片链接 | 图片链接 | text | 首选 `image_list[0].info_list[scene=WB_DFT].url` |
| 视频链接 | 视频链接 | text | `get_note_no_water_video(note_id)` (仅视频笔记) |
| 正文图 | 正文图 | attachment | 下载 `image_list[1:]` 后逐张上传（不含封面） |
| 视频附件 | 视频附件 | attachment | 下载视频后上传（仅视频笔记） |
| 图片附件 | 图片附件 | attachment | 下载封面 URL 后上传 |

## 视频笔记分支

如果是视频笔记（`type=video`），编排脚本自动处理：

```powershell
python skills/xhs-single-note-collect/scripts/xhs_collect.py `
  --base-token <TOKEN> --table-id <TBL> --view-id <VIEW> `
  --rows 26 `
  --cookies-str "<COOKIES>"
```

编排脚本会额外调用 `get_note_no_water_video` 获取下载链接并上传。

手工额外步骤：
```powershell
python xhs_api_tool.py call pc get_note_no_water_video --params '{"note_id":"<NOTE_ID>"}'
curl -L -o note_video.mp4 "<VIDEO_URL>"
lark-cli base +record-upload-attachment --base-token <TOKEN> --table-id <TBL> --record-id <REC> --field-id "视频附件" --file ./note_video.mp4 --as user
```
