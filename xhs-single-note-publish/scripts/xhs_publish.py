#!/usr/bin/env python3
"""
xhs_publish.py — Publish a single note from Feishu Base row to XHS.

Usage (PowerShell, multi-line with backtick):
  python xhs_publish.py `
    --base-token <TOKEN> --table-id <TABLE> `
    --row <N> `
    --cookies-str "<COOKIES>"

  python xhs_publish.py `
    --feishu-url "https://my.feishu.cn/base/TOKEN?table=TBL" `
    --row 6 `
    --cookies-file cookies.txt

Dry-run (print payload only, no API call):
  python xhs_publish.py ... --dry-run
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

SKILL_DIR = Path(__file__).resolve().parent.parent
XHS_API_TOOL = SKILL_DIR.parent / "xhs-apis" / "scripts" / "xhs_api_tool.py"
WORK_DIR = Path.cwd()
CACHE_DIR = WORK_DIR / ".xhs_cache"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CODEX_IMAGEGEN_PROMPT = (
    '生成一张小红书笔记封面图，参考这张图的视觉风格。'
    '主题：{title}。'
    '要求：构图饱满，主体清晰突出，色彩和谐有质感，'
    '画面现代高级，在移动端信息流中具有视觉吸引力。'
)


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, **kwargs)


def _larkcli_cmd(*args: str) -> list:
    if sys.platform == "win32":
        ps1 = shutil.which("lark-cli")
        if not ps1:
            candidates = [
                Path.home() / "scoop" / "apps" / "nodejs" / "current" / "bin" / "lark-cli.ps1",
                Path.home() / "AppData" / "Roaming" / "npm" / "lark-cli.ps1",
            ]
            for c in candidates:
                if c.exists():
                    ps1 = str(c)
                    break
        if ps1:
            ps1_path = Path(ps1)
            if ps1_path.suffix.lower() == ".cmd":
                ps1_path = ps1_path.with_suffix(".ps1")
            return ["pwsh", "-NoProfile", "-File", str(ps1_path)] + list(args)
    return ["lark-cli"] + list(args)


def _larkcli_json(*args: str) -> dict:
    cmd = _larkcli_cmd(*args, "--jq", ".")
    proc = _run(cmd)
    if proc.returncode != 0:
        print(f"[ERROR] lark-cli failed: {' '.join(cmd)}", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(1)
    try:
        return json.loads(_strip_ansi(proc.stdout + proc.stderr))
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON decode failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def _get_field_list(base_token: str, table_id: str) -> list:
    result = _larkcli_json(
        "base", "+field-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--limit", "200",
        "--as", "user",
    )
    return result.get("data", {}).get("fields", [])


def _build_field_id_map(base_token: str, table_id: str) -> dict:
    fields = _get_field_list(base_token, table_id)
    return {f["name"]: f["id"] for f in fields}


def _get_record_fields(base_token: str, table_id: str, record_id: str) -> dict:
    result = _larkcli_json(
        "base", "+record-get",
        "--base-token", base_token,
        "--table-id", table_id,
        "--record-id", record_id,
        "--as", "user",
    )
    data = result.get("data", {})
    field_ids = data.get("field_id_list", [])
    field_names = data.get("fields", [])
    rows = data.get("data", [])
    if not rows or not rows[0]:
        return {}
    row = rows[0]
    fields = {}
    for idx, name in enumerate(field_names):
        if idx < len(row):
            val = row[idx]
            if isinstance(val, str):
                m = re.search(r'\]\(([^)]+)\)', val)
                if m:
                    val = m.group(1)
            fields[name] = val
            if field_ids and idx < len(field_ids):
                fields[field_ids[idx]] = val
    return fields


def _download_attachment(file_token: str, name: str, output_dir: Path) -> Path | None:
    output_path = output_dir / name
    proc = _run(
        _larkcli_cmd("docs", "+media-download",
         "--token", file_token,
         "--output", str(output_path),
         "--as", "user"),
        timeout=120,
    )
    if proc.returncode != 0:
        print(f"[ERROR] Download failed for token {file_token}: {proc.stderr}", file=sys.stderr)
        return None
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    return None


def _upload_attachment(base_token: str, table_id: str, record_id: str,
                       field_name: str, file_path: Path) -> bool:
    field_id = _get_field_id(field_name)
    proc = _run(
        _larkcli_cmd("base", "+record-upload-attachment",
         "--base-token", base_token,
         "--table-id", table_id,
         "--record-id", record_id,
         "--field-id", field_id,
         "--file", file_path.name,
         "--as", "user"),
        cwd=file_path.parent,
    )
    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if "MOBILE_ONLY" in stderr or "仅可通过移动端拍摄上传" in stderr:
            print(f"[ERROR] Field '{field_name}' ({field_id}) is MOBILE_ONLY", file=sys.stderr)
            print(f"        Please toggle off this setting in Feishu UI:", file=sys.stderr)
            print(f"        Field Settings > Advanced > Remove '仅可通过移动端拍摄上传'", file=sys.stderr)
        else:
            print(f"[WARN] Upload failed for {record_id} field {field_name}", file=sys.stderr)
            print(stderr, file=sys.stderr)
        return False
    return True


def _resolve_cookies(args) -> str:
    if args.cookies_str:
        return args.cookies_str
    if args.cookies_file:
        path = Path(args.cookies_file)
        if not path.is_absolute():
            path = WORK_DIR / path
        return path.read_text(encoding="utf-8").strip()
    return ""


def _parse_feishu_url(url: str):
    parsed = urlparse(url)
    m = re.search(r'/base/([^/?]+)', parsed.path)
    base_token = m.group(1) if m else ""
    qs = parse_qs(parsed.query)
    table_id = qs.get("table", [""])[0]
    return base_token, table_id


FIELD_ID_MAP: dict = {}


def _init_field_ids(base_token: str, table_id: str):
    global FIELD_ID_MAP
    FIELD_ID_MAP = _build_field_id_map(base_token, table_id)


def _get_field_id(name: str) -> str:
    return FIELD_ID_MAP.get(name, name)


def main():
    parser = argparse.ArgumentParser(
        description="xhs-single-note-publish: Feishu row → XHS creator post_note",
    )
    parser.add_argument("--base-token", help="飞书多维表格 Base Token")
    parser.add_argument("--table-id", help="表 ID")
    parser.add_argument("--feishu-url", help="飞书表格链接（替代 --base-token --table-id）")
    parser.add_argument("--row", help="行号（仅支持单行）", type=int)
    parser.add_argument("--record-id", help="替代 row：直接指定 record_id")
    parser.add_argument("--cookies-str", help="XHS cookies 字符串（需含 creator 域）")
    parser.add_argument("--cookies-file", help="cookies 文本文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只构建 payload 并打印，不实际调用 API")
    args = parser.parse_args()

    if args.feishu_url and not (args.base_token and args.table_id):
        bt, tid = _parse_feishu_url(args.feishu_url)
        args.base_token = bt
        args.table_id = tid

    if not args.base_token or not args.table_id:
        print("[ERROR] 必须提供 --base-token --table-id 或 --feishu-url", file=sys.stderr)
        raise SystemExit(1)
    if not args.row and not args.record_id:
        print("[ERROR] 必须提供 --row 或 --record-id", file=sys.stderr)
        raise SystemExit(1)

    cookies = _resolve_cookies(args)
    if not cookies and not args.dry_run:
        print("[ERROR] 必须提供 cookies (--cookies-str 或 --cookies-file)", file=sys.stderr)
        raise SystemExit(1)

    print("[0/4] Initializing field ID map...", file=sys.stderr)
    _init_field_ids(args.base_token, args.table_id)
    print(f"  → {len(FIELD_ID_MAP)} fields loaded", file=sys.stderr)

    print("[1/4] Reading target row...", file=sys.stderr)

    if args.record_id:
        record_id = args.record_id
    else:
        result = _larkcli_json(
            "base", "+record-list",
            "--base-token", args.base_token,
            "--table-id", args.table_id,
            "--limit", str(max(args.row, 200)),
            "--as", "user",
        )
        rids = result.get("data", {}).get("record_id_list", [])
        if args.row < 1 or args.row > len(rids):
            print(f"[ERROR] Row {args.row} out of range (1-{len(rids)})", file=sys.stderr)
            raise SystemExit(1)
        record_id = rids[args.row - 1]

    fields = _get_record_fields(args.base_token, args.table_id, record_id)

    publish_status = fields.get("发布状态", "")
    if isinstance(publish_status, list):
        publish_status = publish_status[0] if publish_status else ""
    publish_status = str(publish_status).strip()
    print(f"  → Record {record_id}, 发布状态: {publish_status}", file=sys.stderr)

    if publish_status == "已发布":
        print(f"  [SKIP] Already published, nothing to do.", file=sys.stderr)
        return
    if publish_status not in ("待发布", "发布失败", ""):
        print(f"  [SKIP] Status '{publish_status}' not eligible for publishing.", file=sys.stderr)
        return

    selected = fields.get("入选题库？")
    if not selected:
        print(f"  [SKIP] '入选题库？' 未勾选，跳过发布", file=sys.stderr)
        return

    rewritten = fields.get("仿写完成？")
    if not rewritten:
        print(f"  [SKIP] '仿写完成？' 未勾选，跳过发布", file=sys.stderr)
        return

    title = str(fields.get("仿写标题.输出结果", "")).strip()
    desc = str(fields.get("仿写正文.输出结果", "")).strip()
    tags_str = str(fields.get("标签", "")).strip()
    cover_attachments = fields.get("仿写封面")

    if not title:
        print("[ERROR] '仿写标题.输出结果' 为空，无法发布", file=sys.stderr)
        raise SystemExit(1)
    if not desc:
        print("[ERROR] '仿写正文.输出结果' 为空，无法发布", file=sys.stderr)
        raise SystemExit(1)

    tags = [t.strip() for t in tags_str.split(",") if t.strip()]

    print(f"  → title: {title[:40]}...", file=sys.stderr)
    print(f"  → desc: {desc[:50]}...", file=sys.stderr)
    print(f"  → tags: {tags}", file=sys.stderr)

    print("[2/4] Preparing cover image...", file=sys.stderr)
    media_dir = CACHE_DIR / f"publish_{uuid.uuid4().hex[:8]}"
    media_dir.mkdir(parents=True, exist_ok=True)

    if not cover_attachments or (isinstance(cover_attachments, list) and len(cover_attachments) == 0):
        if args.dry_run:
            print(f"  → [DRY-RUN] 仿写封面为空，从 图片附件 取原封面", file=sys.stderr)
            original_cover = fields.get("图片附件")
            if original_cover and (isinstance(original_cover, list) and len(original_cover) > 0):
                cover_attachments = original_cover
            else:
                print("[ERROR] '图片附件' 也无附件，无法生成封面", file=sys.stderr)
                raise SystemExit(1)
        else:
            print(f"  → 仿写封面为空，开始 codex 生图...", file=sys.stderr)

            original_cover = fields.get("图片附件")
            if not original_cover or (isinstance(original_cover, list) and len(original_cover) == 0):
                print("[ERROR] '图片附件' 字段无附件，无法生成封面", file=sys.stderr)
                raise SystemExit(1)

            ref_path = _download_attachment(original_cover[0]["file_token"],
                                            original_cover[0].get("name", "ref.jpg"),
                                            media_dir)
            if not ref_path:
                print("[ERROR] 原封面下载失败", file=sys.stderr)
                raise SystemExit(1)
            print(f"  → Downloaded reference: {ref_path.name}", file=sys.stderr)

            output_path = media_dir / "generated_cover.png"
            prompt_text = f"{CODEX_IMAGEGEN_PROMPT.format(title=title)}，保存为 ./generated_cover.png"

            print(f"  → Running codex exec...", file=sys.stderr)
            codex_proc = _run(
                ["codex", "exec",
                 "-i", str(ref_path.resolve()),
                 "--cd", str(media_dir.resolve()),
                 "--sandbox", "workspace-write",
                 "--full-auto",
                 f"$imagegen {prompt_text}"],
                timeout=120,
            )

            if codex_proc.returncode != 0 or not output_path.exists():
                print(f"[ERROR] codex exec failed", file=sys.stderr)
                if codex_proc.stderr:
                    print(codex_proc.stderr, file=sys.stderr)
                raise SystemExit(1)
            print(f"  → Generated: {output_path.name}", file=sys.stderr)

            if not _upload_attachment(args.base_token, args.table_id, record_id,
                                       "仿写封面", output_path):
                print("[ERROR] 仿写封面上传失败", file=sys.stderr)
                raise SystemExit(1)
            print(f"  → Uploaded to 仿写封面", file=sys.stderr)

            fields = _get_record_fields(args.base_token, args.table_id, record_id)
            cover_attachments = fields.get("仿写封面")
            if not cover_attachments or (isinstance(cover_attachments, list) and len(cover_attachments) == 0):
                print("[ERROR] 仿写封面上传后读取仍为空", file=sys.stderr)
                raise SystemExit(1)
    else:
        print(f"  → 仿写封面已存在，跳过生图", file=sys.stderr)

    cover_token = cover_attachments[0]["file_token"]
    cover_name = cover_attachments[0].get("name", "cover.jpg")
    cover_path = _download_attachment(cover_token, cover_name, media_dir)
    if not cover_path:
        print("[ERROR] Failed to download cover image", file=sys.stderr)
        raise SystemExit(1)
    print(f"  → Downloaded: {cover_path.name}", file=sys.stderr)

    note_info = {
        "title": title,
        "desc": desc,
        "media_type": "image",
        "type": 0,
        "postTime": None,
        "topics": tags,
        "images": [str(cover_path.resolve())],
        "location": None,
    }
    publish_payload = {
        "noteInfo": note_info,
        "cookies_str": cookies,
    }

    if args.dry_run:
        print(f"\n[DRY-RUN] Would post with payload:", file=sys.stderr)
        print(json.dumps({**publish_payload, "cookies_str": "***"}, ensure_ascii=False, indent=2))
        print(f"\n[DRY-RUN] Not calling API, exiting.", file=sys.stderr)
        for p in media_dir.iterdir():
            p.unlink(missing_ok=True)
        media_dir.rmdir()
        return

    print("[3/4] Publishing to XHS...", file=sys.stderr)
    payload_file = CACHE_DIR / f"publish_payload_{uuid.uuid4().hex[:8]}.json"
    payload_file.write_text(json.dumps(publish_payload, ensure_ascii=False), encoding="utf-8")

    out_file = CACHE_DIR / f"publish_result_{uuid.uuid4().hex[:8]}.json"

    proc = _run(
        [sys.executable, str(XHS_API_TOOL),
         "call", "creator", "post_note",
         "--params-file", str(payload_file),
         "--out", str(out_file)],
        timeout=300,
    )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if proc.returncode != 0:
        print(f"[ERROR] post_note failed", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        status_value = "发布失败"
    else:
        print(f"  → post_note returned successfully", file=sys.stderr)
        status_value = "已发布"

    print(f"  → Updating status to '{status_value}'...", file=sys.stderr)
    upsert = {
        _get_field_id("发布状态"): status_value,
    }
    if status_value == "已发布":
        upsert[_get_field_id("发布时间")] = now_str

    json_str = json.dumps(upsert, ensure_ascii=False)
    proc = _run(
        _larkcli_cmd("base", "+record-upsert",
         "--base-token", args.base_token,
         "--table-id", args.table_id,
         "--record-id", record_id,
         "--as", "user",
         "--json", json_str),
    )
    if proc.returncode != 0:
        print(f"[WARN] Status update failed: {proc.stderr}", file=sys.stderr)
    else:
        print(f"    Done ({status_value}).", file=sys.stderr)

    payload_file.unlink(missing_ok=True)
    for p in media_dir.iterdir():
        p.unlink(missing_ok=True)
    media_dir.rmdir()

    summary = {
        "ok": status_value == "已发布",
        "record_id": record_id,
        "status": status_value,
        "title": title,
        "timestamp": now_str,
    }
    try:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()