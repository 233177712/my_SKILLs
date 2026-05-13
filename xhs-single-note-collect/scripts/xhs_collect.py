#!/usr/bin/env python3
"""
xhs_collect.py — One-shot workflow: Feishu Base → xhs-apis → write-back.

Usage (PowerShell, multi-line with backtick):
  python xhs_collect.py `
    --base-token <TOKEN> --table-id <TABLE> --view-id <VIEW> `
    --rows 26,27,28 `
    --cookies-str "<COOKIES>"

  python xhs_collect.py `
    --base-token <TOKEN> --table-id <TABLE> --view-id <VIEW> `
    --rows 26,27,28 `
    --cookies-file cookies.txt

Cache/writeback modes:
  python xhs_collect.py ... --use-cache-only   # no XHS API calls, cache only
  python xhs_collect.py ... --writeback-only   # skip collection, only write cached
  python xhs_collect.py ... --skip-note-info   # skip get_note_info
  python xhs_collect.py ... --skip-user-info   # skip get_user_info
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
                Path(os.environ.get("SCOOP", r"C:\Users\14541\scoop")) / "apps" / "nodejs" / "current" / "bin" / "lark-cli.ps1",
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


def _larkcli(*args: str) -> str:
    cmd = _larkcli_cmd(*args)
    proc = _run(cmd)
    if proc.returncode != 0:
        print(f"[ERROR] lark-cli failed: {' '.join(cmd)}", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(1)
    return _strip_ansi(proc.stdout + proc.stderr)


def _larkcli_json(*args: str) -> dict:
    cmd = _larkcli_cmd(*args, "--format", "json")
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
    cmd = _larkcli_cmd("base", "+field-list",
                        "--base-token", base_token,
                        "--table-id", table_id,
                        "--limit", "200",
                        "--as", "user",
                        "--jq", ".")
    proc = _run(cmd)
    if proc.returncode != 0:
        print(f"[ERROR] lark-cli +field-list failed", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(1)
    try:
        result = json.loads(_strip_ansi(proc.stdout + proc.stderr))
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON decode failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    return result.get("data", {}).get("fields", [])


def _build_field_id_map(base_token: str, table_id: str) -> dict:
    fields = _get_field_list(base_token, table_id)
    return {f["name"]: f["id"] for f in fields}


def _get_record_list(base_token: str, table_id: str, view_id: str, limit: int = 200) -> list:
    result = _larkcli_json(
        "base", "+record-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--view-id", view_id,
        "--limit", str(limit),
        "--as", "user",
    )
    data = result.get("data", {})
    field_ids = data.get("field_id_list", [])
    field_names = data.get("fields", [])
    rows = data.get("data", [])
    record_ids = data.get("record_id_list", [])
    link_field_idx = None
    for idx, name in enumerate(field_names):
        if name == "小红书链接":
            link_field_idx = idx
            break
    records = []
    for i, row in enumerate(rows):
        if not row:
            continue
        rec = {"record_id": record_ids[i] if i < len(record_ids) else "", "url": ""}
        if field_ids:
            rec["field_ids"] = field_ids
        if field_names:
            rec["field_names"] = field_names
        if link_field_idx is not None and link_field_idx < len(row):
            url_val = row[link_field_idx]
            if isinstance(url_val, str):
                m = re.search(r'\]\(([^)]+)\)', url_val)
                if m:
                    rec["url"] = m.group(1)
                else:
                    rec["url"] = url_val
        records.append(rec)
    return records


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


def _count_attachments(fields: dict, field_name: str) -> int:
    val = fields.get(field_name)
    if val is None:
        return 0
    if isinstance(val, list):
        return len(val)
    s = str(val).strip()
    if s in ("", "[]", "null", "None"):
        return 0
    return 1


def _field_has_value(fields: dict, field_name: str) -> bool:
    value = str(fields.get(field_name, "")).strip()
    return value not in ("", "[]", "null", "None")


def _get_record_url(base_token: str, table_id: str, record_id: str) -> str:
    fields = _get_record_fields(base_token, table_id, record_id)
    for key in ("小红书链接",):
        if key in fields:
            return fields[key]
    print(f"[WARN] No 小红书链接 found for {record_id}", file=sys.stderr)
    return ""


def _extract_note_id(url: str) -> str:
    m = re.search(r'/explore/([a-f0-9]{24})', url)
    if m:
        return m.group(1)
    m = re.search(r'/discovery/item/([a-f0-9]{24})', url)
    if m:
        return m.group(1)
    return url


def _cache_path(note_id: str, method: str) -> Path:
    return CACHE_DIR / f"{note_id}_{method}.json"


def _load_cache(note_id: str, method: str) -> dict | None:
    path = _cache_path(note_id, method)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_cache(note_id: str, method: str, data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(note_id, method).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _call_xhs_api(method: str, params: dict, tag: str, cache_key: str = ""):
    if cache_key:
        cached = _load_cache(cache_key, method)
        if cached:
            print(f"  (cached) {method}", file=sys.stderr)
            return cached

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    params_file = CACHE_DIR / f"payload_{tag}_{uuid.uuid4().hex[:8]}.json"
    params_file.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")

    out_file = CACHE_DIR / f"result_{tag}_{uuid.uuid4().hex[:8]}.json"

    cmd = [
        sys.executable, str(XHS_API_TOOL),
        "call", "pc", method,
        "--params-file", str(params_file),
        "--out", str(out_file),
    ]
    proc = _run(cmd)

    params_file.unlink(missing_ok=True)

    if proc.returncode != 0:
        if out_file.exists():
            try:
                data = json.loads(out_file.read_text(encoding="utf-8"))
                return data
            except (json.JSONDecodeError, OSError):
                pass
        print(f"[ERROR] xhs_api_tool.py {method} failed", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return None

    if out_file.exists():
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
            if cache_key:
                _save_cache(cache_key, method, data)
            return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] Could not read {out_file}: {e}", file=sys.stderr)
            return None
    return None


def _parse_count(s) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).strip()
    if not s or s == "-1":
        return -1
    if "万" in s:
        return int(float(s.replace("万", "")) * 10000)
    if "亿" in s:
        return int(float(s.replace("亿", "")) * 100000000)
    try:
        return int(s)
    except ValueError:
        return -1


def _extract_note_data(api_result: dict) -> dict | None:
    try:
        result_list = api_result.get("result", [])
        if len(result_list) < 3:
            return None
        data = result_list[2].get("data", {})
        items = data.get("items", [])
        if not items:
            return None
        card = items[0].get("note_card", {})
    except (IndexError, KeyError, TypeError):
        return None

    note_id = card.get("note_id", "")
    note_type = card.get("type", "normal")
    desc = card.get("desc", "")
    title = card.get("title", "").strip()
    if not title:
        first_sentence = re.split(r'[。！？.!?\n]', desc)[0].strip()
        if first_sentence:
            title = first_sentence
        else:
            title = desc[:15]
    user = card.get("user", {})
    user_id = user.get("user_id", "")
    nickname = user.get("nickname", "")
    interact = card.get("interact_info", {})
    ts = card.get("time", 0)
    publish_time = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else ""

    tag_list = card.get("tag_list", [])
    tags = ", ".join(t.get("name", "") for t in tag_list if t.get("name"))

    image_list = card.get("image_list", [])
    cover_url = ""
    if image_list:
        info_list = image_list[0].get("info_list", [])
        for info in info_list:
            if info.get("image_scene") == "WB_DFT":
                cover_url = info.get("url", "")
                break
        if not cover_url:
            cover_url = image_list[0].get("url_default", "")

    return {
        "note_id": note_id,
        "type": note_type,
        "desc": desc,
        "title": title,
        "user_id": user_id,
        "nickname": nickname,
        "liked_count": _parse_count(interact.get("liked_count", "0")),
        "collected_count": _parse_count(interact.get("collected_count", "0")),
        "comment_count": _parse_count(interact.get("comment_count", "0")),
        "share_count": _parse_count(interact.get("share_count", "0")),
        "tags": tags,
        "publish_time": publish_time,
        "cover_url": cover_url,
        "image_list": image_list,
    }


def _extract_user_data(api_result: dict) -> dict | None:
    try:
        result_list = api_result.get("result", [])
        if len(result_list) < 3:
            return None
        data = result_list[2].get("data", {})
        interactions = data.get("interactions", [])
    except (IndexError, KeyError, TypeError):
        return None

    fans = 0
    interaction = 0
    for item in interactions:
        t = item.get("type", "")
        if t == "fans":
            fans = _parse_count(item.get("count", "0"))
        elif t == "interaction":
            interaction = _parse_count(item.get("count", "0"))

    return {"fans": fans, "interaction": interaction}


def _extract_video_download_url(api_result: dict) -> str:
    try:
        result_list = api_result.get("result", [])
        if len(result_list) < 3 or not result_list[0]:
            return ""
        return result_list[2] or ""
    except (IndexError, KeyError, TypeError):
        return ""


def _download_file(url: str, dest: Path) -> bool:
    try:
        proc = _run(["curl", "-L", "-o", str(dest), url], timeout=120)
        return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0
    except Exception as e:
        print(f"[WARN] Download failed: {url[:60]}... — {e}", file=sys.stderr)
        return False


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


def _get_best_image_url(image_list: list, index: int = 0) -> str:
    if index >= len(image_list):
        return ""
    img = image_list[index]
    info_list = img.get("info_list", [])
    for info in info_list:
        if info.get("image_scene") == "WB_DFT":
            return info.get("url", "")
    return img.get("url_default", "")


def _download_body_images(note_data: dict, temp_dir: Path) -> list[Path]:
    body_paths = []
    for idx in range(1, len(note_data.get("image_list", []))):
        img_url = _get_best_image_url(note_data["image_list"], idx)
        if not img_url:
            continue
        body_path = temp_dir / f"body_{note_data['note_id'][:12]}_{idx}.jpg"
        if _download_file(img_url, body_path):
            body_paths.append(body_path)
            print(f"  → Downloaded body image: {body_path.name}", file=sys.stderr)
    return body_paths


def _writeback_fields(base_token: str, table_id: str, record_id: str,
                      data: dict, now_str: str):
    video_download_url = data.get("video_download_url", "")
    if not video_download_url and data.get("type") != "video":
        video_download_url = "不涉及"

    upsert = {
        _get_field_id("作者名"): data.get("nickname", ""),
        _get_field_id("标题"): data.get("title", ""),
        _get_field_id("正文"): data.get("desc", ""),
        _get_field_id("标签"): data.get("tags", ""),
        _get_field_id("点赞数"): data.get("liked_count", -1),
        _get_field_id("评论数"): data.get("comment_count", -1),
        _get_field_id("分享数"): data.get("share_count", -1),
        _get_field_id("收藏数"): data.get("collected_count", -1),
        _get_field_id("发布时间"): data.get("publish_time", ""),
        _get_field_id("提取时间"): now_str,
        _get_field_id("粉丝"): data.get("fans", -1),
        _get_field_id("获赞与收藏"): data.get("interaction", -1),
        _get_field_id("图片链接"): data.get("cover_url", ""),
        _get_field_id("视频链接"): video_download_url,
    }
    upsert = {k: v for k, v in upsert.items() if v != -1}

    json_str = json.dumps(upsert, ensure_ascii=False)
    proc = _run(
        _larkcli_cmd("base", "+record-upsert",
         "--base-token", base_token,
         "--table-id", table_id,
         "--record-id", record_id,
         "--as", "user",
         "--json", json_str),
    )
    if proc.returncode != 0:
        print(f"[WARN] Write-back failed for {record_id}", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
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
    view_id = qs.get("view", [""])[0]
    return base_token, table_id, view_id


def _ask(header: str, default: str = "") -> str:
    prompt = f">> {header}"
    if default:
        prompt += f" (default: {default})"
    prompt += ": "
    val = input(prompt).strip()
    return val or default


def _interactive_prompt():
    print("=" * 60, file=sys.stderr)
    print("  小红书笔记采集 — 交互模式", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(file=sys.stderr)

    # 1. Cookies
    print("[步骤 1/3] 提供小红书 Cookies", file=sys.stderr)
    print("  支持两种方式:", file=sys.stderr)
    print("    a) 直接粘贴 cookies 字符串", file=sys.stderr)
    print("    b) 输入 cookies 文件路径", file=sys.stderr)
    print(file=sys.stderr)
    choice = _ask("请选择 (a/b)")
    cookies = ""
    if choice.lower() == "b":
        path_str = _ask("cookies 文件路径")
        path = Path(path_str)
        if not path.is_absolute():
            path = WORK_DIR / path
        if path.exists():
            cookies = path.read_text(encoding="utf-8").strip()
        else:
            print(f"[ERROR] File not found: {path}", file=sys.stderr)
            raise SystemExit(1)
    else:
        cookies = _ask("粘贴 cookies 字符串 (一行)")
        if not cookies:
            print("[ERROR] cookies 不能为空", file=sys.stderr)
            raise SystemExit(1)
    print(file=sys.stderr)

    # 2. Feishu URL
    print("[步骤 2/3] 提供飞书多维表格链接", file=sys.stderr)
    print("  格式: https://my.feishu.cn/base/TOKEN?table=TBL&view=VIEW", file=sys.stderr)
    print(file=sys.stderr)
    feishu_url = _ask("飞书表格链接")
    base_token, table_id, view_id = _parse_feishu_url(feishu_url)
    if not base_token:
        print("[ERROR] 无法解析飞书链接", file=sys.stderr)
        raise SystemExit(1)
    print(f"  解析结果: base-token={base_token}, table-id={table_id}, view-id={view_id}", file=sys.stderr)
    print(file=sys.stderr)

    # 3. Rows
    print("[步骤 3/3] 指定要采集的行号", file=sys.stderr)
    print("  格式: 逗号分隔，例如 26,27,28", file=sys.stderr)
    print(file=sys.stderr)
    rows = _ask("行号")
    if not rows:
        print("[ERROR] 行号不能为空", file=sys.stderr)
        raise SystemExit(1)
    print(file=sys.stderr)

    print("[确认] 即将开始采集:", file=sys.stderr)
    print(f"  Base Token : {base_token}", file=sys.stderr)
    print(f"  Table ID   : {table_id}", file=sys.stderr)
    print(f"  View ID    : {view_id}", file=sys.stderr)
    print(f"  Rows       : {rows}", file=sys.stderr)
    print(f"  Cookies    : {'✓ (已提供)' if cookies else '✗ (未提供)'}", file=sys.stderr)
    print(file=sys.stderr)
    confirm = _ask("确认执行? (Y/n)", default="Y")
    if confirm.lower() not in ("y", "yes", ""):
        print("已取消", file=sys.stderr)
        raise SystemExit(0)

    return base_token, table_id, view_id, rows, cookies


FIELD_ID_MAP: dict = {}


def _init_field_ids(base_token: str, table_id: str):
    global FIELD_ID_MAP
    FIELD_ID_MAP = _build_field_id_map(base_token, table_id)


def _get_field_id(name: str) -> str:
    return FIELD_ID_MAP.get(name, name)


def main():
    parser = argparse.ArgumentParser(
        description="xhs-single-note-collect: Feishu → xhs-apis → write-back",
    )
    parser.add_argument("--base-token",
                        help="飞书多维表格 Base Token")
    parser.add_argument("--table-id",
                        help="表 ID")
    parser.add_argument("--view-id",
                        help="视图 ID（决定行号顺序）")
    parser.add_argument("--rows",
                        help="行号，逗号分隔，如 26,27,28")
    parser.add_argument("--record-ids",
                        help="替代 rows：直接指定 record_id")
    parser.add_argument("--feishu-url",
                        help="飞书表格链接（替代 --base-token --table-id --view-id）")
    parser.add_argument("--cookies-str",
                        help="XHS cookies 字符串 (a1=...; web_session=...)")
    parser.add_argument("--cookies-file",
                        help="Path to a file containing cookies string")
    parser.add_argument("--skip-comments", action="store_true",
                        help="Skip comment collection (faster)")
    parser.add_argument("--skip-media", action="store_true",
                        help="Skip media download+upload (text only)")
    parser.add_argument("--use-cache-only", action="store_true",
                        help="Use only cached data, never call XHS API")
    parser.add_argument("--writeback-only", action="store_true",
                        help="Skip all collection, only write cached data to Feishu")
    parser.add_argument("--skip-note-info", action="store_true",
                        help="Skip get_note_info (use cache)")
    parser.add_argument("--skip-user-info", action="store_true",
                        help="Skip get_user_info (use cache)")
    args = parser.parse_args()

    if args.use_cache_only:
        args.skip_note_info = True
        args.skip_user_info = True
    if args.writeback_only:
        args.skip_note_info = True
        args.skip_user_info = True
        args.skip_comments = True
        args.skip_media = True

    # ── Pre-step: interactive prompt if required params missing ──
    has_cli_args = args.base_token and args.table_id and args.view_id and args.rows
    has_url = bool(args.feishu_url)
    has_cookies = bool(args.cookies_str or args.cookies_file)

    if not has_cli_args and not has_url:
        print("[PRE-STEP] 缺少必要参数，进入交互模式...", file=sys.stderr)
        print("（提示: 下次可直接传参跳过交互，详见 --help）", file=sys.stderr)
        print(file=sys.stderr)
        bt, tid, vid, rows, cookies = _interactive_prompt()
        args.base_token = bt
        args.table_id = tid
        args.view_id = vid
        args.rows = rows
        if cookies:
            tmp_cookie = CACHE_DIR / f"cookies_{uuid.uuid4().hex[:8]}.txt"
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp_cookie.write_text(cookies, encoding="utf-8")
            args.cookies_file = str(tmp_cookie)
    elif has_url and not has_cli_args:
        bt, tid, vid = _parse_feishu_url(args.feishu_url)
        args.base_token = bt
        args.table_id = tid
        args.view_id = vid

    if not args.base_token or not args.table_id or not args.view_id:
        print("[ERROR] 必须提供飞书表格参数 (--base-token/--table-id/--view-id 或 --feishu-url)", file=sys.stderr)
        raise SystemExit(1)
    if not args.rows and not args.record_ids:
        print("[ERROR] 必须提供 --rows 或 --record-ids", file=sys.stderr)
        raise SystemExit(1)

    cookies = _resolve_cookies(args)
    if not cookies:
        print("[ERROR] 必须提供 cookies (--cookies-str 或 --cookies-file)", file=sys.stderr)
        raise SystemExit(1)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Clean old payload/result files in cache dir
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("payload_*.json"):
            f.unlink(missing_ok=True)
        for f in CACHE_DIR.glob("result_*.json"):
            f.unlink(missing_ok=True)

    print("[1/6] Initializing field ID map...", file=sys.stderr)
    _init_field_ids(args.base_token, args.table_id)
    print(f"  → {len(FIELD_ID_MAP)} fields loaded", file=sys.stderr)

    print("[1/6] Reading Feishu record list...", file=sys.stderr)
    all_records = _get_record_list(args.base_token, args.table_id, args.view_id)
    print(f"  → {len(all_records)} records in view", file=sys.stderr)

    if args.record_ids:
        target_ids = [r.strip() for r in args.record_ids.split(",")]
    else:
        row_numbers = [int(r.strip()) for r in args.rows.split(",")]
        target_ids = []
        for rn in row_numbers:
            if 1 <= rn <= len(all_records):
                target_ids.append(all_records[rn - 1]["record_id"])
            else:
                print(f"[ERROR] Row {rn} out of range (1-{len(all_records)})", file=sys.stderr)
                raise SystemExit(1)

    print("[2/6] Fetching note URLs...", file=sys.stderr)
    target_records = []
    for rid in target_ids:
        url = _get_record_url(args.base_token, args.table_id, rid)
        note_id = _extract_note_id(url)
        target_records.append({"record_id": rid, "url": url, "note_id": note_id})
        print(f"  → {rid}: {note_id or url[:50]}...", file=sys.stderr)

    seen_notes = {}
    for rec in target_records:
        key = rec["note_id"]
        if key not in seen_notes:
            seen_notes[key] = {
                "note_url": rec["url"],
                "record_ids": [],
                "note_data": None,
                "user_data": None,
            }
        seen_notes[key]["record_ids"].append(rec["record_id"])

    unique_notes = list(seen_notes.values())
    total_unique = len(unique_notes)
    total_records = len(target_records)
    duplicates = total_records - total_unique
    if duplicates > 0:
        print(f"  → {duplicates} duplicate note(s) detected, will collect only {total_unique} unique note(s)", file=sys.stderr)

    for idx, note in enumerate(unique_notes, 1):
        note_id = _extract_note_id(note["note_url"])
        tag = note_id[:12] if note_id else f"note{idx}"

        print(f"\n[3/6-{idx}/{total_unique}] Collecting note: {note_id} ({','.join(note['record_ids'])})", file=sys.stderr)

        params = {"url": note["note_url"], "cookies_str": cookies}
        note_data = _load_cache(note_id, "note_data")
        if note_data:
            print(f"  (cached) note_data", file=sys.stderr)
        elif args.skip_note_info:
            print(f"  [SKIP] --skip-note-info and no cache, skipping", file=sys.stderr)
            continue
        else:
            print(f"  → get_note_info (may wait 60-120s for rate limit)...", file=sys.stderr)
            note_result = _call_xhs_api("get_note_info", params, f"{tag}_info", cache_key=note_id)
            if note_result is None:
                print(f"  [SKIP] get_note_info failed, skipping note", file=sys.stderr)
                continue
            note_data = _extract_note_data(note_result)
            if note_data is None:
                print(f"  [SKIP] Could not parse note data, skipping", file=sys.stderr)
                continue
        note["note_data"] = note_data
        print(f"  → {note_data['nickname']}: {note_data['title'][:40]}...", file=sys.stderr)
        print(f"    likes={note_data['liked_count']} collects={note_data['collected_count']} comments={note_data['comment_count']} shares={note_data['share_count']}", file=sys.stderr)

        # Save cache for resume
        _save_cache(note_id, "note_data", note_data)

        if note_data["user_id"]:
            user_data = _load_cache(note_id, "user_data")
            if user_data:
                print(f"  (cached) user_data", file=sys.stderr)
                note["user_data"] = user_data
                print(f"    fans={user_data['fans']} interaction={user_data['interaction']}", file=sys.stderr)
            elif not args.skip_user_info:
                print(f"  → get_user_info...", file=sys.stderr)
                user_params = {"user_id": note_data["user_id"], "cookies_str": cookies}
                user_result = _call_xhs_api("get_user_info", user_params, f"{tag}_user", cache_key=note_id)
                if user_result:
                    user_data = _extract_user_data(user_result)
                    if user_data:
                        note["user_data"] = user_data
                        _save_cache(note_id, "user_data", user_data)
                        print(f"    fans={user_data['fans']} interaction={user_data['interaction']}", file=sys.stderr)

        if not args.skip_comments:
            if _load_cache(note_id, "comments_done"):
                print(f"  (cached) comments", file=sys.stderr)
            else:
                print(f"  → get_note_all_comment...", file=sys.stderr)
                _call_xhs_api("get_note_all_comment", params, f"{tag}_comment", cache_key=note_id)
                _save_cache(note_id, "comments_done", {"done": True})
                print(f"    (comments saved, not written to table)", file=sys.stderr)

        if note_data.get("type") == "video":
            if note_data.get("video_download_url"):
                print(f"  (cached) video url", file=sys.stderr)
            else:
                print(f"  → get_note_no_water_video...", file=sys.stderr)
                video_result = _call_xhs_api(
                    "get_note_no_water_video",
                    {"note_id": note_id},
                    f"{tag}_video",
                    cache_key=note_id,
                )
                if video_result:
                    video_download_url = _extract_video_download_url(video_result)
                    if video_download_url:
                        note_data["video_download_url"] = video_download_url
                        _save_cache(note_id, "note_data", note_data)
                        print(f"    video url ready", file=sys.stderr)

    print(f"\n[6/6] Writing back to Feishu...", file=sys.stderr)

    for idx, note in enumerate(unique_notes, 1):
        note_data = note.get("note_data")
        user_data = note.get("user_data")
        if not note_data:
            note_id = _extract_note_id(note["note_url"])
            note_data = _load_cache(note_id, "note_data")
            if not note_data:
                continue
            note["note_data"] = note_data
            user_data = _load_cache(note_id, "user_data")
            note["user_data"] = user_data

        if user_data:
            note_data["fans"] = user_data["fans"]
            note_data["interaction"] = user_data["interaction"]
        else:
            note_data["fans"] = -1
            note_data["interaction"] = -1

        temp_dir = CACHE_DIR / f"media_{uuid.uuid4().hex[:8]}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        cover_path = None
        body_image_paths = []
        video_path = None

        if not args.skip_media:
            cover_url = note_data.get("cover_url", "")
            if cover_url:
                cover_path = temp_dir / f"cover_{note_data['note_id'][:12]}.jpg"
                if _download_file(cover_url, cover_path):
                    print(f"  → Downloaded cover: {cover_path.name}", file=sys.stderr)
                else:
                    cover_path = None

            if note_data.get("type") == "video":
                video_download_url = note_data.get("video_download_url", "")
                if video_download_url:
                    video_path = temp_dir / f"video_{note_data['note_id'][:12]}.mp4"
                    if _download_file(video_download_url, video_path):
                        print(f"  → Downloaded video: {video_path.name}", file=sys.stderr)
                    else:
                        video_path = None
            else:
                body_image_paths = _download_body_images(note_data, temp_dir)

        for rid in note["record_ids"]:
            print(f"  → Writing {rid}...", file=sys.stderr)
            record_fields = _get_record_fields(args.base_token, args.table_id, rid)

            if cover_path and not args.skip_media:
                existing_cover = _count_attachments(record_fields, "图片附件")
                if existing_cover == 0:
                    _upload_attachment(args.base_token, args.table_id, rid,
                                       "图片附件", cover_path)
                else:
                    print(f"    cover already present ({existing_cover} file(s)), skipping", file=sys.stderr)
            if not args.skip_media:
                expected_body = len(body_image_paths)
                if expected_body > 0:
                    existing_body = _count_attachments(record_fields, "正文图")
                    if existing_body < expected_body:
                        for body_image_path in body_image_paths:
                            _upload_attachment(args.base_token, args.table_id, rid,
                                               "正文图", body_image_path)
                    else:
                        print(f"    body images already complete ({existing_body}, expected {expected_body}), skipping", file=sys.stderr)
                if video_path:
                    existing_video = _count_attachments(record_fields, "视频附件")
                    if existing_video == 0:
                        _upload_attachment(args.base_token, args.table_id, rid,
                                           "视频附件", video_path)
                    else:
                        print(f"    video already present ({existing_video} file(s)), skipping", file=sys.stderr)

            _writeback_fields(args.base_token, args.table_id, rid,
                              note_data, now_str)
            print(f"    Done.", file=sys.stderr)

        if temp_dir.exists():
            for p in temp_dir.iterdir():
                p.unlink(missing_ok=True)
            temp_dir.rmdir()

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Completed: {total_records} record(s) processed, {total_unique} unique note(s) collected.", file=sys.stderr)
    if duplicates > 0:
        print(f"(Saved {duplicates} duplicate API call(s) via dedup)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    summary = {
        "ok": True,
        "records_processed": total_records,
        "unique_notes": total_unique,
        "duplicates_skipped": duplicates,
        "timestamp": now_str,
        "details": [
            {
                "record_ids": note["record_ids"],
                "note_id": _extract_note_id(note["note_url"]),
                "nickname": (note.get("note_data") or {}).get("nickname", ""),
                "title": (note.get("note_data") or {}).get("title", ""),
            }
            for note in unique_notes
        ],
    }
    summary_json = json.dumps(summary, ensure_ascii=False, indent=2)
    try:
        print(summary_json)
    except UnicodeEncodeError:
        print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
