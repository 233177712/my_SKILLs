# 编码问题排查指南

在 xhs-single-note-collect（及其依赖）的 shell 命令执行中，编码问题是最高频的故障来源。以下是完整排查路径。

## 症状速查表

| 症状 | 根因 | 一句话解法 |
|------|------|-----------|
| `'gbk' codec can't encode character '\U0001f430'` | GBK 终端输出 emoji | 用 `--out` 写文件 + `$env:PYTHONIOENCODING='utf-8'` |
| PowerShell 输出全是 `�����` | 终端编码与输出编码不匹配 | `Out-File -Encoding UTF8` 存文件，用 Python 或 VS Code 打开 |
| `ConvertFrom-Json` 报错 | ANSI 彩色转义码混在 JSON 里 | 先 `$text -replace '\x1b\[[0-9;]*m', ''` 去 ANSI 码 |
| `lark-cli` 输出到 `2>` 文件为空 | PowerShell 的 `2>` 对原生命令不生效 | 用 `2>&1 | Out-File` |
| Select-String 中文匹配不到 | Select-String 默认按当前代码页解码 | `Select-String -Encoding UTF8` |
| `curl` 下载的文件名乱码 | URL 含中文被 GBK 转义 | 用 Python 的 `urllib.request` 替代 |

## 编码链路全景

```
lark-cli (UTF-8) ──→ stdout/stderr ──→ PowerShell 管道 ──→ 你的眼睛 / 其他命令
                   ↑ ANSI escape sequences    ↓ 默认按 GBK 解码
                                              └─→ GBK 解码 UTF-8 → 乱码
```

### 关键事实

1. **lark-cli 输出是 UTF-8**，且混有 ANSI 彩色转义码（`\x1b[31;1m` 等）
2. **PowerShell 的 `>  ` 和 `|` 使用当前代码页**（中文 Windows = GBK 936）
3. **原生 exe 的 stderr 通过 `2>&1` 合并后管道传输**
4. **`Select-String` 默认使用当前代码页解码文件内容**（除非指定 `-Encoding UTF8`）
5. **Python 的 `print()` 使用 `sys.stdout.encoding`**（通常是 gbk）
6. **curl 下载的文件名按 shell 编码处理**

## 解决方案

### 方案 A：用编排脚本（推荐）

编排脚本 `xhs_collect.py` 内部统一 UTF-8，绕过所有编码问题：

```powershell
python skills/xhs-single-note-collect/scripts/xhs_collect.py ...
```

### 方案 B：手工命令正确写法

#### 保存 lark-cli 输出

```powershell
# ✅ 正确
lark-cli base +record-list ... 2>&1 | Out-File -Encoding UTF8 output.txt -Width 9999

# ❌ 错误
lark-cli base +record-list ... 2> output.txt   # 文件为 0 字节
lark-cli base +record-list ... > output.txt     # 只捕获 stdout，丢 stderr
```

#### 解析 JSON 中的中文

```powershell
# ✅ 用 Python 读取 UTF-8 文件
python -c "
import json
d = json.load(open('output.txt', 'r', encoding='utf-8'))
print(d['data']['records'][0]['id'])
"

# ✅ 或设环境变量后直接 pip
$env:PYTHONIOENCODING='utf-8'
python -c "import json; d=json.load(open('output.txt')); print(d['data']['records'][0]['id'])"
```

#### 用 Python 搜索含中文的文本

```powershell
# ✅ Python regex 替代 Select-String
python -c "
import re
with open('output.txt', 'r', encoding='utf-8') as f:
    for line in f:
        if re.search(r'作者名|标题|点赞数', line):
            print(line.rstrip())
"

# ❌ 避免：Select-String 默认 GBK
Select-String "作者名" output.txt    # 匹配不到
```

#### 修复 Python print 时的 UnicodeEncodeError

```powershell
# ✅ 方法 1：设环境变量
$env:PYTHONIOENCODING='utf-8'

# ✅ 方法 2：用 xhs_api_tool.py 的 --out 参数
python xhs_api_tool.py call pc get_note_info --params-file p.json --out result.json

# ✅ 方法 3：手工捕获异常
python -c "
import json, sys
data = {'text': '\U0001f430'}
try:
    print(json.dumps(data, ensure_ascii=False))
except UnicodeEncodeError:
    print(json.dumps(data, ensure_ascii=False).encode('utf-8', errors='replace').decode('utf-8'))
"
```

#### 去掉 ANSI 转义码

```powershell
# PowerShell
$text = [regex]::Replace($text, '\x1b\[[0-9;]*m', '')

# Python
import re; text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
```

## 常见错误恢复

### Q: `lark-cli base +record-list` 输出被截断

行内容过大（尤其含长正文和封面分析），导致控制台截断。使用：

```powershell
lark-cli base +record-list ... 2>&1 | Out-File -Encoding UTF8 output.txt -Width 9999
```

然后用 `Get-Content output.txt | Select-String "recv"` 查看行号。

### Q: 终端全是 `□` 或 `?`

Python 脚本输出到 GBK 终端时遇到不可映射字符。用 `--out` 写文件，不用 print。

### Q: `xhs_api_tool.py` 报错但 `--out` 文件已经生成了

这是 v1 工具的已知问题（已在本次优化中修复）：print 在 --out 写文件之后执行，但 print 抛 UnicodeEncodeError 导致进程返回非零退出码。**out 文件是完整的**，可以直接读取使用。

## 一次性环境修复

在会话开始时执行一次，可减少大部分编码问题：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$PSDefaultParameterValues['Select-String:Encoding'] = 'utf8'
```
