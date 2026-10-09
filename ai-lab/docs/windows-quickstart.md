# Windows 上手指南（PowerShell 补充版）

> **这是 macOS/Linux 文档的 Windows 补充版。** 本仓库其他文档（根 `README.md`、`week01/README.md`、
> `python_basics/README.md`、`python_basics/js_to_python.md`、`scripts/load_to_postgres.sh`）里的
> `python3` / `export` / `source` / `cp` / `./xxx.sh` **都不是 PowerShell 语法，照抄会报错** ——
> 先用 §1 对照表替换，再回原文档继续。仓库根 `G:\转型`，命令可直接复制运行。

## 0. 先确认终端，再记住三条铁律

```powershell
$PSVersionTable.PSVersion   # 5.1.x = 系统自带；7.x = PowerShell 7
```

系统自带的 **5.1** 能用，但不支持 `&&`、默认编码是 GBK、写文件默认带 BOM；建议装 PowerShell 7：
`winget install Microsoft.PowerShell`。

1. **不要用 `&&` 连接命令**（5.1 会报"不是有效的语句分隔符"），用换行或 `;`。
2. **多行续行用反引号**，它必须是行尾最后一个字符，后面不能有空格。
3. **`<` `>` 是 PowerShell 保留字符**：`<你的用户名>` 这类占位符不要连尖括号一起复制。

### 0.1 本机实测结论（2026-10，这台机器）

这几条是**实际踩出来的**，不是从文档抄的。同一台机器上的其他人大概率会遇到同样的问题：

| 事实 | 影响 |
|---|---|
| **本机没有 `pwsh`**（只有 5.1） | `pwsh` 相关的建议暂时用不上；所有脚本要按 5.1 的规则写 |
| **`.ps1` 脚本必须带 UTF-8 BOM** | 5.1 读**无 BOM** 的 UTF-8 脚本时按 GBK 解码，中文注释会吃掉引号，报出**完全误导人**的 `The string is missing the terminator` / `Missing expression after ','`，行号还指到别处 |
| **`pip` 的配置文件在中文路径下不可用** | 路径含中文时，pip 读 `PIP_CONFIG_FILE` 会报 `Configuration file contains invalid cp936 characters`。把文件内容改成纯 ASCII 也没用 —— 出问题的是**路径**。改用命令行 `--index-url` |
| **`pypi.org` 不可达，清华/阿里镜像可达** | 直连 pypi 会**挂住几分钟**而不是立刻报错，很像"卡死"。一律加 `--index-url` |
| **系统原先没有任何独立 Python** | `python` 只是 Microsoft Store 占位符（报 `Python was not found...`）。已装 `G:\Python\Python312` |

**给仓库里 `.ps1` 脚本加 BOM 的方法**（改完脚本后如果又出现"字符串缺少终止符"，就是这个原因）：

```powershell
# 用仓库的 .venv 跑一次，把无 BOM 的 .ps1 补上 BOM
& G:\转型\.venv\Scripts\python -c @"
import pathlib
for f in [r'G:\转型\setup.ps1']:
    p = pathlib.Path(f); raw = p.read_bytes()
    if not raw.startswith(b'\xef\xbb\xbf'):
        p.write_bytes(b'\xef\xbb\xbf' + raw); print('added BOM:', f)
"@
```

`.gitattributes` 里已经写了 `*.ps1 text eol=crlf working-tree-encoding=UTF-8`，
所以**新 clone 出来的脚本是带 BOM 的**，不需要每次手动补。

> ⚠️ 编辑器注意：VS Code 右下角显示 `UTF-8 with BOM` 才是对的。如果显示 `UTF-8`，
> 用它另存为 "UTF-8 with BOM"。**这条只对 `.ps1` 重要** ——
> `.py` / `.md` / `.sh` 一律**不要** BOM（会破坏 shebang，也让 diff 变脏）。

---

## 1. 命令对照表（bash → PowerShell）

| 你在其他文档里看到的 | Windows PowerShell 正确写法 | 关键差异 |
|---|---|---|
| `python3 script.py` | `py -3 script.py` 或 `python script.py` | **Windows 没有 `python3`** |
| `python3 -m venv .venv` | `py -3 -m venv .venv` | 逻辑完全一样 |
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` | 是 `Scripts` 不是 `bin`；**前面的 `.\` 不能省** |
| 同上（在 cmd 里） | `.venv\Scripts\activate.bat` | |
| `pip install httpx` | `python -m pip install httpx` | 保证装进当前 venv |
| `export KEY=value` | `$env:KEY = "value"` | 只对**当前终端窗口**有效 |
| `KEY=value python a.py` | 拆两行：先 `$env:KEY="value"`，再 `py -3 a.py` | PowerShell **没有**命令前置变量语法 |
| `which python` / `where python` | `Get-Command python` / `where.exe python` | `where` 是 `Where-Object` 别名，**必须写 `where.exe`** |
| `cp a b` | `Copy-Item a b` | `cp -r` 会因参数名有歧义而失败 |
| `rm -rf dir` | `Remove-Item -Recurse -Force dir` | `rm -rf` 不能照抄 |
| `ls` / `cat f` | `Get-ChildItem` / `Get-Content f` | `ls`、`cat` 是别名，简单场景能跑 |
| `cmd &`（后台运行） | `Start-Job { cmd }` 或 `Start-Process cmd` | PowerShell 的 `&` 是**调用运算符**，不是后台 |
| `cmd1 && cmd2` | `cmd1; cmd2`（PS 7 才支持 `&&`） | |
| `./script.sh` | `bash script.sh`（Git Bash），或改写成 `.ps1` | 见 §5.3 |
| `curl -H ... -d ...` | `curl.exe -H ... -d ...` | PS 5.1 的 `curl` 是 `Invoke-WebRequest`，参数完全不同 |

W0 验收项"用 `curl` 调通 `/chat/completions`"必须写 `curl.exe`（PS 5.1 的 `curl` 是 `Invoke-WebRequest`，
`-H`/`-d` 会报参数错误）；`-N` 关掉缓冲才能看到逐字流式，中文乱码就把 JSON 存成文件用 `-d "@body.json"`：

```powershell
$body = '{"model":"deepseek-chat","stream":true,"messages":[{"role":"user","content":"你好"}]}'
curl.exe -N -s https://api.deepseek.com/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer $env:OPENAI_API_KEY" -d $body
```

---

## 2. 一次性环境搭建（按顺序执行）

```powershell
# 2.0 【最省事】一键完成 2.1–2.4：探测解释器 → 建 .venv → 装依赖 → 自检
Set-Location G:\转型
.\setup.ps1
```

`setup.ps1` 会依次尝试 `G:\Python\Python312`、`C:\Python312`、`%LOCALAPPDATA%\Programs\Python\...`、
PATH 里的 `python` / `py`，并**跳过 Microsoft Store 的占位符**（那个"存在但不可用"，
运行它只会提示你去商店装）。探测失败时会打印可直接粘贴的安装命令。

下面是它替你做的事，手写一遍也可以 —— **但照抄时注意版本与路径**：

```powershell
# 2.1 装 Python 3.11+ 并验证（-0 是数字零，不是字母 O）
winget install Python.Python.3.12
py -3 --version       # 期望 3.11+
py -0p                # 列出本机所有 Python 及路径
where.exe python      # 看 PATH 解析到哪个 python.exe
```

- 用**官网安装包**装的话，第一屏务必勾选 **Add python.exe to PATH** 和 **py launcher**；winget 装完若
  `python` 仍找不到，**关掉终端重开**（PATH 变更不会注入已开的窗口），仍不行就重跑安装包补勾选。
  包 ID 与版本以 <https://www.python.org/downloads/windows/> 和 `winget search Python.Python` 为准。
- `py` 是 Windows 的 **Python 启动器**（PEP 397），`py -3` = 最新 Python 3，比 `python` 更可靠。
- **本机实测**：`winget` 是否可用未验证；实际是用官网安装包静默装的，装在
  `G:\Python\Python312`（装到 G 盘是因为 C 盘只剩 30GB）：
  ```powershell
  $u = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
  Invoke-WebRequest $u -OutFile "$env:TEMP\py312.exe"
  Start-Process "$env:TEMP\py312.exe" -Wait -ArgumentList `
    '/quiet','InstallAllUsers=0','TargetDir=G:\Python','PrependPath=1','Include_launcher=1'
  ```
  注意 `TargetDir=G:\Python` 会被当作**父目录**，实际装到 `G:\Python\Python312\`。

```powershell
# 2.2 建虚拟环境（建在**仓库根** G:\转型，不是 ai-lab）
Set-Location G:\转型
& G:\Python\Python312\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
# 报"禁止运行脚本"就先执行一次（详见 §6）：
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# 退出虚拟环境：deactivate

# 2.3 装依赖（国内必须加镜像，直连 pypi.org 会挂住）
$mirror = @('--index-url','https://pypi.tuna.tsinghua.edu.cn/simple','--timeout','30')
python -m pip install -q @mirror --upgrade pip
python -m pip install -q @mirror fastapi "uvicorn[standard]" httpx pytest pytest-asyncio

# 2.4 环境自检
python ai-lab\tools\env_report.py
```

`"uvicorn[standard]"` 的**方括号在 PowerShell 里是通配符，必须加引号**。**每个新开的终端都要重新激活**：
忘了激活不会报错，只会把包装到全局（见 §6）。`check_env.py` 的修复提示仍是 macOS 写法
（`export ...`、`python3 ...`），按 §1 替换即可；它的 ✅/❌ 是 ANSI 字符，老式控制台可能显示成 `←[32m`，
换 Windows Terminal 就正常。计划 §2 还要求 `uv` 或 `poetry` 二选一（本项目非必需），最省事是
`python -m pip install uv`，之后 2.2–2.3 可换成 `uv venv` + `uv pip install ...`（见 <https://docs.astral.sh/uv/>）。

---

## 3. 跑通仓库里的练习

### 3.1 三个零依赖脚本（`python_basics/`）

`03_chunk_text.py` 不需要网络、不需要 Key，**先跑它确认环境没问题**：

```powershell
Set-Location G:\转型\ai-lab\python_basics
py -3 03_chunk_text.py
```

`01` / `02` 需要模型端点，且它们**只读环境变量、不读 `.env`**（这点和 `week01/chat.py` 不同）：

```powershell
# 终端 1：启动本地 mock（不消耗额度），结束按 Ctrl+C
Set-Location G:\转型\ai-lab\week01
py -3 mock_server.py

# 终端 2：先设变量，再运行
Set-Location G:\转型\ai-lab\python_basics
$env:OPENAI_BASE_URL = "http://127.0.0.1:8765/v1"; $env:OPENAI_API_KEY = "test"
py -3 01_hello_llm.py
py -3 02_stream_chat.py
```

### 3.2 `week01/mock_server.py` + `chat.py`：在 PowerShell 里开两个终端

```powershell
wt -w 0 nt -d G:\转型\ai-lab\week01     # Windows Terminal 新标签页，并进入该目录
```

没装 Windows Terminal 就 `winget install Microsoft.WindowsTerminal`，或再开一个 PowerShell 窗口、在 VS Code
里按 `Ctrl+Shift+5` 分屏。**`$env:` 变量是"每个终端进程一份"**：终端 1 设过的，终端 2 看不到，必须各自设。

```powershell
# ---- 方式 A：两个终端 + 临时环境变量（原文档的写法）----
# 终端 1
Set-Location G:\转型\ai-lab\week01
py -3 mock_server.py

# 终端 2
Set-Location G:\转型\ai-lab\week01
$env:OPENAI_BASE_URL = "http://127.0.0.1:8765/v1"
$env:OPENAI_API_KEY  = "test"
py -3 chat.py
```

原文档的 `OPENAI_BASE_URL=... OPENAI_API_KEY=test python3 chat.py` 是 bash 的"命令前置变量"，
PowerShell **不支持**，必须拆成"先设变量、再运行"两行。

```powershell
# ---- 方式 B：用 .env 文件（一劳永逸，推荐）----
Set-Location G:\转型\ai-lab\week01
Copy-Item .env.example .env
code .env            # 或 notepad .env，填 OPENAI_API_KEY
py -3 chat.py
py -3 chat.py --model gpt-4o-mini --temperature 0.3   # 会话内命令：/exit /clear /stats /temp 0.7
```

`chat.py` 的优先级是 **命令行参数 > 环境变量 > `.env`**，所以当前终端设的 `$env:OPENAI_BASE_URL`
能覆盖 `.env`，随时切回 mock。注意**用 `Copy-Item`（保留原始字节）生成 `.env`，不要用
`Get-Content | Set-Content` 重建**：PS 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，
`OPENAI_API_KEY` 变成 `\ufeffOPENAI_API_KEY`，表现为"明明填了 key 却提示缺少 API Key"
（脚本里的 `strip()` 不会去掉 BOM）。数据生成器同理，只需换解释器：
`py -3 scripts\generate_saas_data.py --users 1200 --events 9000 --tenants 12`。

---

## 4. Git 与 GitHub

**仓库根是 `G:\转型`（不是 `ai-lab`），已 `git init` 并完成首次提交，分支 `main`。**
学习计划那份 Markdown 就在仓库根，和代码同仓库最方便，所以 git 命令都在 `G:\转型` 下执行。

```powershell
# 4.1 初次配置（只需一次；换新机器时才需要）
git config --global user.name  "你的名字"
git config --global user.email "you@example.com"
git config --global init.defaultBranch main
git config --global core.longpaths true      # 防 Windows 260 字符路径限制
git config --global --list                   # 检查

# 4.2 首次提交（仓库已建好；仅当 git status 报 not a git repository 时才要 git init）
Set-Location G:\转型
git add .
git commit -m "W0: 初始化学习仓库"

# 4.3 关联远端并推送：先在 GitHub 建空仓库，别勾 Add README / .gitignore，避免首次冲突
git remote add origin "https://github.com/你的用户名/转型.git"
git branch -M main
git push -u origin main
```

以后每次：`git add .` → `git commit -m "..."` → `git push`。改远端：`git remote set-url origin "新地址"`。
推送认证用 Git for Windows 自带的 **Git Credential Manager**（首次 push 弹浏览器登录），
或 `winget install GitHub.cli` 后 `gh auth login`。

**`.gitignore` 已就位**（仓库根一份、`ai-lab/` 一份）：忽略 `ai-lab/data/out/*.csv`（可重建）、
`.venv/`、`__pycache__/`、`.env`（密钥，但保留 `.env.example`）、`*.sqlite3`、`.DS_Store` ——
所以 `git add .` 不会把密钥提交上去。

### 4.4 CRLF 换行问题（Windows 必看）

Windows 默认 CRLF、macOS/Linux 是 LF。不配置的话每次提交都会刷 `warning: LF will be replaced by CRLF`；
更糟的是**把 CRLF 的 `.sh` 提交上去**，在 Git Bash / Docker / Linux 里会报
`bad interpreter: No such file or directory` 或 `exec format error`。

```powershell
git config --global core.autocrlf input     # 本仓库推荐
```

| 取值 | 提交时 | 检出时 | 适合 |
|---|---|---|---|
| `true` | CRLF → LF | LF → CRLF | 只在 Windows 上开发 |
| **`input`** | CRLF → LF | 原样 LF | **本仓库**：有 `.sh`，以后要进 Docker/Linux |
| `false` | 不动 | 不动 | 配合 `.gitattributes` 精细控制 |

已经乱了就 `git add --renormalize .` 后重新提交。**仓库根已自带 `.gitattributes`**（`* text=auto eol=lf`，
并单独钉死 `*.sh` 用 LF、`*.ps1`/`*.bat`/`*.cmd` 用 CRLF），它随仓库走、换机器不失效，与 `core.autocrlf` 冲突时以它为准。

---

## 5. Postgres + pgvector（W5 之后要用，二选一）

### 方案 A：Docker Desktop（推荐，pgvector 尤其必须）

```powershell
winget install Docker.DockerDesktop
# 装完启动 Docker Desktop，等托盘图标变绿，别只输命令就往下走
docker run -d --name saas-pg `
  -p 5432:5432 `
  -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=saas_lab `
  -e "POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C" `
  pgvector/pgvector:pg16

docker ps     # 确认容器在跑
docker exec -i saas-pg psql -U postgres -d saas_lab -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

- 镜像 tag 与可用版本以 <https://github.com/pgvector/pgvector> 的 README 为准（`pg16`/`pg17` 均有）。
- 运维 `docker stop saas-pg` / `docker start saas-pg` / `docker rm -f saas-pg`（删除即清库）；
  5432 被占用就改 `-p 5433:5432`。
- Docker 方案下宿主机**没有 `psql`**，`load_to_postgres.sh` 不能直接用 → 见 §5.3。

### 方案 B：原生安装（pgvector 要自己编译，不推荐）

```powershell
winget search PostgreSQL      # 在结果里挑一个（常见如 PostgreSQL.PostgreSQL.16），ID 以搜索结果为准
# 或用 EnterpriseDB 官方安装包，以 https://www.postgresql.org/download/windows/ 为准

$env:Path += ";C:\Program Files\PostgreSQL\16\bin"   # 只对当前终端有效；永久生效在"系统属性→环境变量"里加
$env:PGUSER     = "postgres"          # Windows 上当前用户名默认不是数据库超级用户，必须指定
$env:PGPASSWORD = "安装时设置的密码"   # 避免每次交互式输密码
createdb -E UTF8 -T template0 --locale=C saas_lab
psql -l                               # 确认 Encoding 是 UTF8
```

不加 `$env:PGUSER` / `-U postgres` 会报 `FATAL: role "Administrator" does not exist`（macOS 上 Homebrew
会把你的系统用户建成超级用户，所以那边的文档从不写 `-U`）。中文 locale 的安装若 `template1` 不是 UTF8，
导入 UTF-8 CSV 会报编码错误，故上面显式指定 `-E UTF8`。**pgvector 原生编译要 Visual Studio Build Tools
+ nmake，Windows 上很折腾，W5 请直接用方案 A。**

### 5.3 `load_to_postgres.sh` 的 PowerShell 版

bash 版就四件事：`createdb` → `psql -f data/schema.sql` → `TRUNCATE` → 按外键顺序 `\copy` 五个 CSV。

```powershell
Set-Location G:\转型\ai-lab        # 在仓库根执行：\copy 的相对路径是相对 psql 的当前目录解析的
$env:PGCLIENTENCODING = "UTF8"     # CSV 是 UTF-8，不设会因客户端编码不一致而报错

psql -q -d saas_lab -f data/schema.sql
psql -q -d saas_lab -c "TRUNCATE tenants, users, subscriptions, events, payments RESTART IDENTITY CASCADE;"
foreach ($t in "tenants","users","subscriptions","events","payments") {
  psql -q  -d saas_lab -c "\copy $t FROM 'data/out/$t.csv' WITH (FORMAT csv, HEADER true)"
  psql -tA -d saas_lab -c "SELECT COUNT(*) FROM $t;"
}
```

**路径反斜杠是最大的坑**：psql 里 `\` 是转义字符，`'G:\转型\ai-lab\data\out\tenants.csv'` 会被解析坏，
`\copy` 的路径**一律写正斜杠**（`'G:/转型/ai-lab/data/out/tenants.csv'`），或者像上面那样只用相对路径。
若仍报"找不到文件"（Windows 中文路径 + psql 客户端编码的已知摩擦），把 CSV 复制到纯 ASCII 路径再导。

**Docker 起的库（方案 A）**：宿主机没 psql，把数据塞进容器，顺带绕开中文路径问题：

```powershell
docker cp .\data\out saas-pg:/tmp/out
foreach ($t in "tenants","users","subscriptions","events","payments") {
  docker exec -i saas-pg psql -U postgres -d saas_lab -c "\copy $t FROM '/tmp/out/$t.csv' WITH (FORMAT csv, HEADER true)"
}
```

想把上面这段留成可复用脚本：新建 `scripts\load_to_postgres.ps1`（本文档不替你创建该文件），开头加
`param([string]$Db = "saas_lab", [switch]$InDocker)` 和 `$OutputEncoding = [System.Text.Encoding]::UTF8`
（PS 5.1 管道默认 ASCII，不设会毁掉中文），其余照抄上面的 `foreach`；被策略拦截时用
`powershell -ExecutionPolicy Bypass -File .\scripts\load_to_postgres.ps1`。

验证（原文档命令，注意不用 `python3`，且时间窗口固定不要用 `now()`）：
`psql -d saas_lab -c "SELECT COUNT(*) FROM events;"`

---

## 6. 常见报错速查表

| 报错 / 现象 | 原因 | 处理 |
|---|---|---|
| `python : 无法将"python"项识别为 cmdlet`、`'python' 不是内部或外部命令` | 没装 / 没勾 Add to PATH | 重装并勾选，或关掉终端重开；临时用 `py -3` |
| `python3` 找不到，或运行 `python3` 弹出微软商店 | **Windows 没有 `python3`**，那是应用执行别名（stub） | 用 `py -3`；在"设置→应用→高级应用设置→应用执行别名"里关掉它 |
| `无法加载文件 ...run.ps1，因为在此系统上禁止运行脚本`（或 `未对文件进行数字签名`） | 执行策略拦住了 `.ps1` | 见下面「执行策略：为什么改了还是被拦」——**光改策略往往不够，要新开窗口** |
| 中文乱码 / 方块 | 控制台代码页不是 UTF-8 | `chcp 65001`；`[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`；`$env:PYTHONUTF8="1"`；用 Windows Terminal |
| `UnicodeDecodeError: 'gbk' codec can't decode byte ...` | 读 UTF-8 文件时用了 GBK 默认编码 | 代码里显式 `encoding="utf-8"`；或 `$env:PYTHONUTF8 = "1"`（§7） |
| `UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'` | 输出被重定向到文件/管道时按 GBK 编码 | 同上，或 `py -3 -X utf8 script.py` |
| `OSError: [WinError 10048]` / `Address already in use` | 端口被占用（8765 / 8000 / 5432 最常见） | `Get-NetTCPConnection -LocalPort 8765 -State Listen \| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`（或用 `netstat -ano \| Select-String ":8765"` 查到 PID）；或换端口 `py -3 mock_server.py --port 8766` 并同步改 `OPENAI_BASE_URL` |
| `pip install` 成功但 `import httpx` 失败 | 装到全局了，脚本用的是另一个解释器 | 看提示符有没有 `(.venv)`；一律用 `python -m pip install`；`where.exe python`、`python -m pip -V` 确认路径都在 `.venv` 下 |
| 第一行 key 读不到 / `SyntaxError: Non-UTF-8 code starting with '\xff'` | 文件存成了带 BOM 的 UTF-8 | 改存"UTF-8（无 BOM）"；`.env` 用 `Copy-Item` 生成 |
| `标记"&&"不是此版本中的有效语句分隔符` | PS 5.1 不支持 `&&` | 改成 `;` 或换行，或升级 PowerShell 7 |
| `参数名 -r 不明确` / `cp -r` 失败 | `cp` 是 `Copy-Item` 别名，参数不同 | `Copy-Item -Recurse`、`Remove-Item -Recurse -Force` |
| `FATAL: role "Administrator" does not exist` | Windows 默认用户名不是 PG 超级用户 | 设 `$env:PGUSER="postgres"`（+ `$env:PGPASSWORD`）或加 `-U postgres` |
| 多个 Python 打架 | PATH 里有多份 Python | `py -0p` 看清单，用 `py -3.12` 精确定位；`where.exe python` 看优先级 |
| `pip install` **卡住不动**：CPU 一直涨、内存很小、缓存里没有下载文件 | pip 在尝试**源码构建**（本机没装 C 编译器） | 加 `--only-binary :all:` 强制只要预编译 wheel，见下面 |

### pip 装包卡住：为什么必须加 `--only-binary :all:`

**本机实测**：装 numpy 时 `pip install numpy --index-url <清华镜像>` 跑了 **300 秒还没完**。
症状很有迷惑性：

```
CPU 占用    : 301 秒（一直在算）
内存占用    : 只有 47 MB
下载缓存    : 没有任何新文件
镜像连通性  : HTTP 200，0.8 秒响应（网速完全正常）
```

**结论：它不是在下载，是在"解析 + 尝试源码构建"。** 本机没装 MSVC/Build Tools，
pip 找不到预编译 wheel 就会去构建，然后在 CPU 上空转。

**修法 —— 强制只用预编译 wheel：**

```powershell
& G:\转型\.venv\Scripts\python.exe -m pip install numpy `
    --only-binary :all: `
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple `
    --progress-bar off --timeout 30
# 实测：14.4 秒装完（numpy 2.5.3）
```

加上这几个参数后，**装不上就会立刻报错**而不是无限空转 —— 早失败比慢失败好得多。

> ⚠️ **副作用要知道**：纯 Python 包（源码分发，如 `jieba`）会被这个参数**拒绝**，
> 报 `No matching distribution found`。那种包要么去掉 `--only-binary`，
> 要么装 Build Tools。本仓库的依赖都不需要编译，所以默认加上它更安全。

### 执行策略：为什么改了还是被拦

**这是本机实测过的一个坑**：策略显示已经是 `RemoteSigned`，脚本却依然被拦。

原因：**PowerShell 只在启动时读一次执行策略**，之后不再重读。
所以你在窗口 A 里改了策略，**窗口 A 本身仍然按旧策略执行** ——
必须新开窗口，或者给当前窗口单独放行。

**三种处理方式，按推荐顺序：**

```powershell
# ① 只对当前窗口放行（最安全，立即生效，不需要管理员，关掉窗口就恢复）
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run.ps1

# ② 永久改（需要管理员；加 -Force 跳过交互确认）
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
#    改完必须【新开一个 PowerShell 窗口】才生效

# ③ 完全绕开 .ps1（不想碰策略时）
#    照文档里的"手动两个终端"方式，直接用 python.exe 跑
```

**怎么确认当前窗口用的是哪个策略：**

```powershell
Get-ExecutionPolicy -List      # 看所有作用域
Get-ExecutionPolicy            # 看当前生效的
```

> **为什么本仓库的脚本值得放行**：`setup.ps1` / `run.ps1` / `eval.ps1` 都是纯本地操作 ——
> 找 Python、建 venv、装依赖、起服务、跑测试。想确认可以 `notepad` 打开看全文，
> 或者用 `Get-Content .\run.ps1 | Select-String 'Start-Process'` 只看它会启动什么。

---

## 7. 编码与换行：为什么会乱码

Windows 的编码问题来自三层，**叠加**起来才难查：① 控制台代码页（中文 Windows 默认 936/GBK）；
② Python 的文本 I/O 默认用"本地编码"而不是 UTF-8；③ 换行 CRLF vs LF（见 §4.4）。

**`PYTHONUTF8=1` 的作用**：打开 Python 的 **UTF-8 模式**（PEP 540，3.7+），让 `open()` 的默认编码、
标准输入输出、`locale.getpreferredencoding()` 全部变 UTF-8，等于不用在每处都写 `encoding="utf-8"`：

```powershell
$env:PYTHONUTF8 = "1"        # 只对当前终端有效；永久生效（需新开终端）：
# [Environment]::SetEnvironmentVariable("PYTHONUTF8","1","User")
py -3 -c "import locale,sys; print(sys.getfilesystemencoding(), locale.getpreferredencoding(False))"
# 未设时中文 Windows 约等于：utf-8 cp936     设了之后：utf-8 utf-8
```

**Python 3.15 之前 Windows 默认不是 UTF-8（PEP 686 才改成默认 UTF-8）**，典型坑：不带 `encoding`
写文件实际写出的是 GBK 字节（`py -3 -c "open('t.txt','w').write('中文')"`）；读别人用 UTF-8 存的文件
直接抛 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xe4 ...`。受害者集中在：读 CSV/JSON/`.md`、
把提示词写进日志、`subprocess` 起别的程序，以及**把输出重定向到文件**（`py -3 a.py > out.txt`）时
打印 emoji 或生僻字。

**结论**：本地设 `$env:PYTHONUTF8 = "1"`，同时代码里**该写 `encoding="utf-8"` 就写**——后者才是让代码在 macOS/Linux 上同样正确的做法，`PYTHONUTF8=1` 只是本地兜底。

---

## 8. 每次开工的六条命令

```powershell
Set-Location G:\转型                       # 仓库根（git 在这里，.venv 也在这里）
.\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"                      # 防乱码
python ai-lab\tools\env_report.py          # 环境自检
git status                                 # 昨天的改动在哪

# 想跑项目一（本地 mock，不需要 Key）：
cd ai-lab\projects\project1-stream-chat; .\run.ps1
```

> **`.venv` 在仓库根 `G:\转型\.venv`**，不在 `ai-lab\` 下 —— 因为项目分散在
> `ai-lab/projects/*/` 里，共用一套依赖比每个项目建一份更省事（也是 `setup.ps1` 的做法）。
> 早先文档写的是 `ai-lab/.venv`，已统一。

其他文档里的命令跑不通，先回 **§1 对照表**，再查 **§6 报错表**。
