---
name: "xhs-single-note-publish"
description: "从飞书多维表格读取待发布笔记（仿写标题、仿写正文、仿写封面、标签），优先使用仿写封面；为空时基于原图片附件调用 Codex CLI 以图生图仿写封面并回填，再调用小红书创作者平台 post_note API 发布，并回写发布状态与发布时间。"
---

# 小红书单条笔记发布

> 一条命令链完成「读飞书 → 取/生封面 → 发布 → 回写」闭环。

## ⏱ 耗时预估

| 阶段 | 耗时 | 说明 |
|------|------|------|
| 字段映射 + 读取行 | ~3-5s | lark-cli API |
| 下载/生成仿写封面 | ~2-30s | 有仿写封面时直接下载；为空时走 Codex CLI 以图生图并回填 |
| 调用 creator.post_note | ~10-30s | 包含媒体上传 + 签名 + 发布 |
| 回写状态 | ~1-2s | lark-cli API |

**合计约 15-40s**，大部分耗时在 `post_note` 的媒体上传和签名环节。

---

## 快速开始

### 命令行传参

```powershell
python skills/xhs-single-note-publish/scripts/xhs_publish.py `
  --base-token <TOKEN> --table-id <TABLE> `
  --row 6 `
  --cookies-str "<COOKIES>"
```

或使用 `--feishu-url`：

```powershell
python skills/xhs-single-note-publish/scripts/xhs_publish.py `
  --feishu-url "https://my.feishu.cn/base/TOKEN?table=TBL" `
  --row 6 `
  --cookies-file cookies.txt
```

### 脚本参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--base-token` | 按需 | 飞书多维表格 Base Token |
| `--table-id` | 按需 | 表 ID |
| `--feishu-url` | 否 | 飞书表格链接，替代 `--base-token --table-id` |
| `--row` | 按需 | 行号（按视图顺序），与 `--record-id` 二选一 |
| `--record-id` | 否 | 直接指定 record_id，替代 `--row` |
| `--cookies-str` | 否 | XHS cookies 字符串（需含 creator 域） |
| `--cookies-file` | 否 | cookies 文本文件路径 |
| `XHS_COOKIE` | 否 | 环境变量，`--cookies-str` 和 `--cookies-file` 均未提供时读取 |
| `--dry-run` | 否 | 只构建 payload 并打印，不实际调用 API |

> `--dry-run` 模式下 cookies 可不传。非 dry-run 时需通过 `--cookies-str`、`--cookies-file` 或 `XHS_COOKIE` 环境变量之一提供。

### 使用 cookies 文件

```powershell
@"a1=...;web_session=...;access-token-creator.xiaohongshu.com=..."@ | Set-Content -Encoding UTF8 cookies.txt

python skills/xhs-single-note-publish/scripts/xhs_publish.py `
  --feishu-url "https://my.feishu.cn/base/TOKEN?table=TBL" `
  --row 6 `
  --cookies-file cookies.txt
```

### 使用环境变量

设置 `XHS_COOKIE` 环境变量后，无需每次传递 cookies：

```powershell
$env:XHS_COOKIE = "a1=...;web_session=...;access-token-creator.xiaohongshu.com=..."

python skills/xhs-single-note-publish/scripts/xhs_publish.py `
  --feishu-url "https://my.feishu.cn/base/TOKEN?table=TBL" `
  --row 6
```

优先级：`--cookies-str` > `--cookies-file` > `XHS_COOKIE` > 报错

### 仅构建不发布（dry-run）

```powershell
python skills/xhs-single-note-publish/scripts/xhs_publish.py `
  --feishu-url "https://my.feishu.cn/base/TOKEN?table=TBL" `
  --row 6 `
  --dry-run
```

---

## 工作流（4 步）

```
[0/4] 初始化字段 ID 映射    +field-list --jq .
[1/4] 读取目标行，校验状态  +record-get --format json
[2/4] 准备仿写封面           直接下载 / Codex CLI 生成 + 回填
[3/4] 发布 + 更新状态         creator.post_note → +record-upsert
```

## 字段映射

| 用途 | Base 字段 | 类型 | 必填 | 说明 |
|------|-----------|------|------|------|
| 标题 | `仿写标题` | text | 是 | 空时报错 |
| 正文 | `仿写正文.输出结果` | text | 是 | 空时报错 |
| 封面 | `仿写封面` | attachment | 是 | 优先使用；为空时自动读取 `图片附件` 的第一个附件，调用 Codex CLI 生成后回填，再取生成结果作为 images[0] |
| 话题 | `标签` | text | 否 | 逗号拆分后传给 `topics` 参数，API 内部自动 `#话题` 格式化 |

## 状态机

| 当前状态 | 前置条件 | 操作 | 结果状态 |
|----------|----------|------|----------|
| `待发布` | 已入选题库 + 已仿写完成 | 执行发布 | `已发布` / `发布失败` |
| `已发布` | — | 跳过 | — |
| `发布失败` | 已入选题库 + 已仿写完成 | 重试发布 | `已发布` / `发布失败` |

### 发布前置条件（三项必须同时满足）

1. `入选题库？` 已勾选 (checkbox=true)
2. `仿写完成？` 已勾选 (checkbox=true)
3. `发布状态` = `待发布`（或 `发布失败`）

## 发布成功时回写

| Base 字段 | 值 |
|-----------|-----|
| `发布状态` | `已发布` |
| `发布时间` | `YYYY-MM-DD HH:mm:ss`（当前时间） |

---

## 坑点清单

| # | 坑 | 现象 | 规避 |
|---|-----|------|------|
| 1 | cookies 缺少 creator 域 | `post_note` 返回 403/401 | cookies 须包含 `access-token-creator.xiaohongshu.com`、`x-user-id-creator.xiaohongshu.com`、`galaxy_creator_session_id` 等 |
| 2 | `仿写封面` 为空且 `图片附件` 无附件 | 脚本报错退出 | 先确保原笔记封面已经采集到 `图片附件` |
| 3 | Codex CLI 未安装或未登录 | 无法自动仿写封面 | 先执行 `npm install -g @openai/codex` 并完成 `codex login` |
| 4 | `仿写标题` 为空 | 脚本报错退出 | 确保仿写完成后再发布 |
| 5 | `仿写正文.输出结果` 为空 | 脚本报错退出 | 确保仿写完成后再发布 |
| 6 | lark-cli `docs +media-download` 路径 | 某些环境可能不支持 | 脚本自动使用绝对路径下载 |
| 7 | post_note 限流/频率限制 | 连续发布失败 | 每次发布间隔 30s 以上 |
