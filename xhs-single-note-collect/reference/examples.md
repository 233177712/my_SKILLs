# 实战示例

## 示例 1：图文笔记全流程

**场景**：第 25 行笔记，链接 `https://www.xiaohongshu.com/explore/69fdbd90000000001a02d048`

### Step 1: 读字段 + 创建缺失字段

```powershell
lark-cli base +field-list --base-token TMsGbgncba7qdMsHpxfcO8dSnNf --table-id tbltm1ucPvxmwHkA --as user
lark-cli base +field-create --base-token TMsGbgncba7qdMsHpxfcO8dSnNf --table-id tbltm1ucPvxmwHkA --json '{"name":"正文内容","type":"text","style":{"type":"plain"}}' --as user
```

### Step 2: 读链接

```powershell
lark-cli base +record-get --base-token TMsGbgncba7qdMsHpxfcO8dSnNf --table-id tbltm1ucPvxmwHkA --record-id recvju548EqAsw --as user
# → 小红书链接: https://www.xiaohongshu.com/explore/69fdbd90000000001a02d048?xsec_token=...
```

### Step 3: 采笔记

```powershell
# payload_get_note_info.json:
{"url": "https://www.xiaohongshu.com/explore/69fdbd90000000001a02d048?...", "cookies_str": "abRequestId=...;web_session=..."}

python xhs_api_tool.py call pc get_note_info --params-file payload_get_note_info.json --out note_info.json
```

提取结果：
- type: "normal"（图文）
- desc: "AI指令都是通用的，别的平台软件都可以试试#AI聊天 #mufy #rubii"
- user_id: "6800870c000000000d00a6e2"
- nickname: "小菲"
- liked_count: "1.3万" → 13000
- collected_count: "1.5万" → 15000
- comment_count: 383
- share_count: 962
- tags: ["AI聊天", "mufy", "rubii"]
- time: 1778236816000 → 2026-05-08 18:40

### Step 4: 采博主

```powershell
# payload_user_info.json:
{"user_id": "6800870c000000000d00a6e2", "cookies_str": "..."}

python xhs_api_tool.py call pc get_user_info --params-file payload_user_info.json --out user_info.json
```

结果：
- fans: 145
- interaction: 28762

### Step 5: 采评论

```powershell
# payload_comments.json:
{"url": "同上", "cookies_str": "..."}

python xhs_api_tool.py call pc get_note_all_comment --params-file payload_comments.json --out comments.json
```

### Step 6: 回写

```powershell
# 下载 + 上传附件
curl -L -o note_image.jpg "http://sns-webpic-qc.xhscdn.com/..."
curl -L -o note_cover.jpg "https://ci.xiaohongshu.com/..."
lark-cli base +record-upload-attachment --base-token TMsGbgncba7qdMsHpxfcO8dSnNf --table-id tbltm1ucPvxmwHkA --record-id recvju548EqAsw --field-id fld0Jedx1P --file ./note_image.jpg --as user
lark-cli base +record-upload-attachment --base-token TMsGbgncba7qdMsHpxfcO8dSnNf --table-id tbltm1ucPvxmwHkA --record-id recvju548EqAsw --field-id fldcVf4Wts --file ./note_cover.jpg --as user

# 回写字段
lark-cli base +record-upsert --base-token TMsGbgncba7qdMsHpxfcO8dSnNf --table-id tbltm1ucPvxmwHkA --record-id recvju548EqAsw --as user --json '{
  "作者名":"小菲",
  "标题":"AI指令都是通用的，别的平台软件都可以试试",
  "正文内容":"AI指令都是通用的，别的平台软件都可以试试#AI聊天 #mufy #rubii",
  "标签":"AI聊天, mufy, rubii",
  "点赞数":13000,
  "评论数":383,
  "分享数":962,
  "收藏数":15000,
  "发布时间":"2026-05-08 18:40:00",
  "提取时间":"2026-05-13 16:20:00",
  "粉丝":145,
  "获赞与收藏":28762,
  "封面链接":"https://ci.xiaohongshu.com/1040g2sg31vtfiol02qk05q00gs6399n2e4tnuf0?imageView2/format/jpeg"
}'
```

## 示例 2：视频笔记

**关键差异**：
- `type: "video"`，`image_list` 中可能含视频封面
- 额外调 `get_note_no_water_video(note_id)` 获取无水印视频
- 视频文件上传到「正文图/笔记视频」字段
- 「视频下载链接」字段写入 URL

```powershell
# 获取无水印视频
python xhs_api_tool.py call pc get_note_no_water_video --params '{"note_id":"69fdbd90000000001a02d048"}'

# 下载视频（约几十 MB）
curl -L -o note_video.mp4 "http://sns-video-hw.xhscdn.com/stream/..."

# 上传
lark-cli base +record-upload-attachment --base-token <TOKEN> --table-id <TBL> --record-id <REC> --field-id fld0Jedx1P --file ./note_video.mp4 --as user

# 回写含视频链接
lark-cli base +record-upsert --base-token <TOKEN> --table-id <TBL> --record-id <REC> --as user --json '{
  "视频下载链接":"http://sns-video-hw.xhscdn.com/stream/..."
}'
```

## 常见错误恢复

| 错误信息 | 原因 | 修复 |
|----------|------|------|
| `unsafe file path` | `--file` 传了绝对路径 | 改用 `./filename` |
| `Cell value does not match` | JSON 结构多套了 `field_values` | 直接传 `{"字段名":值}` |
| `unknown flag: --name` | lark-cli 参数名错误 | 改用 `--json '{"name":"..."}'` |
| `'gbk' codec can't encode` | 控制台编码不支持 emoji | 用 `--out` 写文件 |
| `sleep 120.9s` | 速率限制 | 等待约 2 分钟自然恢复 |
