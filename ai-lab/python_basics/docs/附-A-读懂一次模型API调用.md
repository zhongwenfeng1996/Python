# 附录 A · 读懂一次模型 API 调用

> **这是一篇查用资料，不是必读章节。**
>
> 读 01–10 章时你会反复看到 `response.get("usage")`、`response["choices"][0]`，
> 但那些章节的重点是 Python 语法，不是 API 结构。想弄清楚 `response` 到底是什么、
> 里面长什么样，就来这里。
>
> **什么时候看**：随时。第 01 章练习 3 会指向这里。

---

## 1. 三步，缺一步都看不懂

所有"调模型"的代码都是这三步。看懂了这三步，`week01/chat.py` 那种 300 行的脚本
你也敢打开看了。

```python
import json
import urllib.request

# ---- 第 1 步：把你要说的话组装成请求 ----
# 这就是一个普通的 Python 字典（第 03 章的内容）
data = {
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "用一句话解释什么是向量"}],
}
body = json.dumps(data, ensure_ascii=False).encode("utf-8")

request = urllib.request.Request(
    "https://api.deepseek.com/v1/chat/completions",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer 你的key",
    },
)

# ---- 第 2 步：发出去，拿回响应 ----
with urllib.request.urlopen(request, timeout=60) as http_response:
    raw = http_response.read().decode("utf-8")   # raw 是一段 JSON 格式的**字符串**

# ---- 第 3 步：把字符串变成 Python 字典 ----
response = json.loads(raw)      # ★ 从这一行起，response 代表"一个嵌套的字典"
```

### 一个容易把人绕晕的地方

教程和真实代码里常把第 2 步和第 3 步合并写成同一个名字，像这样：

```python
with urllib.request.urlopen(request) as response:   # response = HTTP 响应对象
    raw = response.read().decode("utf-8")
response = json.loads(raw)                          # response = 字典（覆盖了上面那个）
```

**同一个名字 `response`，前后是两个完全不同的东西**：
前面是"HTTP 响应对象"（能 `.read()`、有 `.status`），
后面是"Python 字典"（能 `["choices"]`）。

后面所有例子里说的 `response`，都指**第 3 步那个字典**。
你在 `week01/chat.py` 里会看到作者更谨慎，用了不同的名字（`response` 和 `chunk`）。

---

## 2. 这个字典长什么样

一次真实的响应（不是编的，可以用 `week01/show_response.py` 自己跑出来）：

```json
{
  "id": "mock-completion",
  "object": "chat.completion",
  "model": "deepseek-chat",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "向量就是带方向和大小的量，可以理解成一支箭头。"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 11,
    "completion_tokens": 23,
    "total_tokens": 34
  }
}
```

**自己看一眼真的**（不需要 API Key，用仓库自带的 mock）：

```powershell
# 终端 1
py -3 ai-lab\week01\mock_server.py
# 终端 2
$env:OPENAI_BASE_URL = "http://127.0.0.1:8765/v1"; $env:OPENAI_API_KEY = "test"
py -3 ai-lab\week01\show_response.py
```

看到原文，比看任何解释都清楚。

---

## 3. 怎么一层层取出来

这是最该练熟的一段：

```python
response["choices"]                              # list   一个列表
response["choices"][0]                           # dict   列表第 0 项，是字典
response["choices"][0]["message"]                # dict   又是一个字典
response["choices"][0]["message"]["content"]     # str    ★ 模型的回答在这

response["usage"]["prompt_tokens"]               # int    输入用了多少 token
response["usage"]["completion_tokens"]           # int    输出用了多少 token
```

看着绕，规律只有一条：**`[]` 里是数字就是取列表，是字符串就是取字典的键。**

| 写法 | `[]` 里是 | 作用 | 得到 |
|---|---|---|---|
| `response["choices"]` | 字符串 | 取字典的键 | **列表** |
| `[0]` | 数字 | 取列表第 0 项 | **字典** |
| `["message"]` | 字符串 | 取字典的键 | **字典** |
| `["content"]` | 字符串 | 取字典的键 | **字符串** ✅ |

**为什么 `choices` 是列表？** 因为模型可以一次生成多个候选回答（`n` 参数），
所以它必须是列表 —— 哪怕你只要一个，也得写 `[0]`。
这是初学者最常问的"为什么这么绕"的答案。

> 类比 JS：`response.choices[0].message.content`。Python 里字典不能用点号，
> 所以写成中括号链。**只有这一处区别**，别被吓到。

---

## 4. 什么时候用 `[]`，什么时候用 `.get()`

```python
response["usage"]            # 键不存在 -> KeyError，程序崩
response.get("usage")        # 键不存在 -> 返回 None，程序继续
```

判断依据只有一条：**这个键是不是一定存在？**

- `choices`：**一定存在**。模型哪怕回答为空，`choices` 也在。
  所以用 `response["choices"]`，出了问题就该让它报错。
- `usage`：**可能不存在**。不是所有 OpenAI 兼容端点都返回它，
  流式请求下尤其如此。所以用 `.get("usage")`。

### `usage` 的四种可能，值得单独记一下

第 01 章练习 3 就是这道题。完整版本：

| `response.get("usage")` 返回 | `if not usage` 判定 | `if usage is None` 判定 |
|---|---|---|
| `None` —— 键不存在 | 假 → "没有统计" ✅ | 真 → "没有统计" ✅ |
| `{}` —— 键存在但是空字典 | 假 → "没有统计" ✅ | **假 → 放过去** ❌ |
| `{"prompt_tokens": 0, "completion_tokens": 0}` | **真 → 放过去** ❌ | **假 → 放过去** ❌ |
| `{"prompt_tokens": 11, ...}` 正常 | 真 → 正常处理 ✅ | 假 → 正常处理 ✅ |

**两个坑：**

1. `if not usage` 把 `{}` 误判成"没有统计"——因为 `None` 和 `{}` 都是假值
2. **全 0 的字典是真值**，两种写法都会放它过去，于是成本算出来是 $0.0000

第 2 种更坏：你的花费看板会**安静地显示 0**。

### 完整一点的写法

不同厂商字段名还不一样（OpenAI 用 `prompt_tokens`，有些用 `input_tokens`）：

```python
def usable_usage(raw):
    """取出可用的 token 数；取不到就返回 None。"""
    if not isinstance(raw, dict):        # None、字符串、别的类型，一律当没有
        return None
    prompt = raw.get("prompt_tokens") or raw.get("input_tokens") or 0
    completion = raw.get("completion_tokens") or raw.get("output_tokens") or 0
    if not prompt and not completion:    # 全 0（或字段缺失）也算"没有"
        return None
    return int(prompt), int(completion)
```

> `isinstance` 是第 08 章的内容，这里不展开，看代码能猜到意思就行。

**然后关键是：判成"没有"之后干什么？**

```python
usage = usable_usage(response.get("usage"))
if usage is None:
    # ❌ 不要只 print 一句就完了 —— 那 usage 后面用的时候还是错的
    # ✅ 要给出一个可用的替代值，让程序能继续干活
    in_tokens = estimate_tokens(prompt)
    out_tokens = estimate_tokens(answer)
else:
    in_tokens, out_tokens = usage
```

> **只打印日志的兜底等于没兜底。** 这是这一节最该带走的结论。
>
> 真实实现见 [`week01/chat.py`](../../week01/chat.py) 第 282–296 行
> （取不到 usage 就回退到 `estimate_tokens`）和
> [项目一的 `cost.py`](../../projects/project1-stream-chat/backend/cost.py) 的 `usage_from_api()`。
> 一共 20 行，建议读一遍 —— 本节的代码就是从那儿简化来的。

---

## 5. 流式响应不一样

上面讲的是**一次性返回**（`"stream": false`）。流式请求（`"stream": true`）时：

- 服务端不是返回一个完整 JSON，而是返回**很多行 SSE 数据**，一行一个 chunk
- 每行的格式是 `data: {"choices":[{"delta":{"content":"一"}}]}` —— 注意是 `delta` 不是 `message`
- 最后一行是 `data: [DONE]`
- `usage` 通常只在**最后一个 chunk** 里出现，而且不是所有端点都支持

所以要一行行读、把 `delta.content` 拼起来：

```python
with urllib.request.urlopen(request, timeout=60) as http_response:
    for raw_line in http_response:                  # 每次一行，是 bytes
        line = raw_line.decode("utf-8").strip()     # ★ 先变成字符串
        if line == "data: [DONE]":
            break
        if not line.startswith("data:"):
            continue                                # 跳过空行 / 心跳
        chunk = json.loads(line[5:])
        piece = chunk["choices"][0]["delta"]["content"]   # 注意 delta
        print(piece, end="", flush=True)
```

> **别踩这个坑**：`for line in http_response` 拿到的是 **bytes**，
> 不做 `.decode("utf-8")` 就写 `line == "data: [DONE]"`，比较会**永远为 False**
> —— bytes 和 str 比较**不报错**，只是静默不相等，于是 `break` 永远不触发、
> 循环把整个流读完却什么都不做。
>
> 这是"不报错但行为全错"的典型案例，第 10 章有一段完整的排查过程
> （见「一个真实的调试过程」）。想自己实测字节比较的行为，
> 可以跑 `week01/probe_iteration.py`。

---

## 6. 这一篇和哪几章有关

| 附录里的内容 | 对应的章节 |
|---|---|
| 字典、列表、嵌套取值 | [第 03 章](03-列表字典与推导式.md) |
| `if not x` 与真假值 | [第 01 章第 5 节](01-变量与类型.md) |
| `for` 循环、`break` / `continue` | [第 05 章](05-条件循环与作用域.md) |
| bytes 与 str 不能混着比 | [第 05 章](05-条件循环与作用域.md)、[第 10 章](10-调试与报错.md) |
| 把逻辑封装成函数 | [第 04 章](04-函数.md) |
| `isinstance`、`def` 写类（Pydantic） | [第 08 章](08-类与对象.md) |
| 并发调多个模型 | [第 09 章](09-异步与并发.md) |

**读完 01–10 章再回来看这篇，会发现几乎每一条都对上号了。**
这就是这篇附录放在最后的原因 —— 它是"回头看会更清楚"的那类资料。
