# 小红书单条笔记采集

采集小红书单条笔记的全链路数据（正文、媒体、互动、博主），回写到飞书多维表格。

## 安装

确保已安装依赖：

```powershell
pip install -r $env:USERPROFILE\.agents\skills\xhs-apis\scripts\requirements.txt
```

飞书登录：

```powershell
lark-cli auth login --domain base
```

## 快速开始

```powershell
lark-cli base +record-get --base-token <TOKEN> --table-id <TBL_ID> --record-id <REC_ID> --as user
# 拿到笔记链接后，按 SKILL.md 6 步流程执行
```

## 输出效果

回写后飞书表格中自动填充的字段：

- 作者名、标题、正文内容
- 标签（话题）
- 点赞数、评论数、分享数、收藏数
- 发布时间、提取时间
- 粉丝数、获赞与收藏
- 封面链接、视频下载链接
- 封面图片（附件）、正文图/笔记视频（附件）

## 说明

- 图文笔记：正文图/笔记视频 字段上传图片；视频下载链接留空
- 视频笔记：正文图/笔记视频 字段上传视频；视频下载链接写入无水印 URL
- 数据采集时间戳写入「提取时间」字段
