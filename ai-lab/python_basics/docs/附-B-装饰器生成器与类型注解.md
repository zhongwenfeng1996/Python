# 附录 B · 装饰器、生成器与类型注解

> **这是一篇查用资料，不是必读章节。**
>
> 读正课 10 章时，你会反复撞见三样「每个字都认识、但连起来看不懂」的东西：
> `@dataclass`、`yield`、`-> str`。它们分别是**装饰器、生成器、类型注解**。
> 正课为了不打断节奏，只在用到时一笔带过（第 08 章的 `@dataclass`、第 04 章的 `-> dict`）。
> 想真正搞清楚它们是什么、为什么这么写，就来这里。
>
> **什么时候看**：开始读 FastAPI / 开源项目之前，值得通读一遍。
> 这三样是「读懂别人代码」的硬门槛——你自己写可能用不到，但读框架代码天天见。

---

## 1. 装饰器：给函数"包一层"

### 1.1 先看问题

第 04 章讲了函数。现在假设你想给一个函数**计时**，第一反应可能是改函数本体：

```python
import time

def call_model(prompt):
    start = time.time()
    # ... 一大堆业务逻辑 ...
    print(f"耗时 {time.time() - start:.2f}s")
```

问题来了：**你有 10 个函数都要计时**，难道每个都塞一遍 `start = time.time()`？
更糟的是，计时逻辑和业务逻辑**缠在一起**，想单独测试、单独删掉都很难。

装饰器解决的就是这个：**不改函数本体，给它外面套一层新功能。**

### 1.2 装饰器就是一个"函数工厂"

```python
import time

def timed(func):                      # ① 接收一个函数
    def wrapper(*args, **kwargs):     # ② 返回一个新的"包装函数"
        start = time.time()
        result = func(*args, **kwargs)   # ③ 包装函数里调用原函数
        print(f"{func.__name__} 耗时 {time.time() - start:.2f}s")
        return result
    return wrapper

# 用法一：手动包一层（等价于下面的 @ 写法）
def slow_fn():
    time.sleep(1)

slow_fn = timed(slow_fn)             # 把 slow_fn 替换成"被包过的 slow_fn"
slow_fn()                            # slow_fn 耗时 1.00s
```

```python
# 续上文：这里假设上面已经定义了 `timed` 装饰器（`import time` 也还在）
# 用法二：@ 语法糖（等价于上面的 slow_fn = timed(slow_fn)）
@timed
def another_fn():
    time.sleep(1)

another_fn()                         # another_fn 耗时 1.00s
```

**关键认知**：`@timed` 这行**不是魔法**，它就是 `another_fn = timed(another_fn)` 的缩写。
`timed` 接收函数、返回一个新函数，把原来的名字替换掉。

> **前端对照**：最像的是高阶函数 `const timed = (fn) => (...args) => {...}`。
> 但 Python 有 `@` 语法把它写成声明式，而 JS 要手动赋值。
> 注意 `*args, **kwargs` 是第 04 章讲的"接收任意参数"，这里用来保证包装函数
> 能接受原函数的任何参数。

### 1.3 你在哪见过装饰器

其实你早就见过了，只是没意识到：

| 装饰器 | 作用 | 在哪章 |
|---|---|---|
| `@dataclass` | 自动生成 `__init__` / `__repr__` / `__eq__` | 第 08 章 |
| `@property` | 把方法变成"属性"（`obj.total` 而不是 `obj.total()`） | 第 08 章 |
| `@app.get("/")` | FastAPI：把一个函数注册成路由 | 后面做项目时 |
| `@pytest.fixture` | pytest：声明测试夹具 | 后面写测试时 |

所以「读不懂装饰器，就读不懂 FastAPI 代码」——这不是夸张。

### 1.4 常见坑

| 坑 | 说明 |
|---|---|
| 以为 `@timed` 会"改变"原函数 | 不，它返回**新函数**，你拿到的是新函数。原函数本体还在 |
| 包装函数忘记 `return result` | 被装饰的函数调用会返回 `None`，功能"消失" |
| 忘记 `*args, **kwargs` | 原函数有参数时，包装函数接不住，报参数错误 |

---

## 2. 生成器：函数里的"暂停"和"继续"

### 2.1 问题：数据太大，一次装不下

假设你要把一个大文件逐行处理。第 06 章讲的做法是：

```python
def read_lines(path):
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            lines.append(line)
    return lines              # 一次性返回所有行
```

如果文件有 1 亿行，`lines` 这个列表会占满内存。**能不能"要一行给一行"，而不是攒齐了再给？**

### 2.2 `yield`：把 return 换成"逐个给"

```python
def read_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            yield line        # ★ 关键：不是 return，是 yield
```

调用它：

```python
# 续上文：read_lines 是上面那个用 yield 的生成器函数
gen = read_lines("big.txt")
print(next(gen))              # 第一行
print(next(gen))              # 第二行（函数从上次 yield 处继续）
```

**关键认知**：
- `return` 是"结束，一次性给结果"
- `yield` 是"暂停，给一个值；下次 `next()` 从暂停处继续"
- 含 `yield` 的函数，调用它**不会执行函数体**，而是返回一个"生成器对象"

### 2.3 为什么 AI 应用里处处是生成器

**流式输出（SSE）本质就是生成器。** 模型一个字一个字地吐，而不是憋完了整段再给你：

```python
# 前置：这段需要下面这些前置（照抄时请一并复制）：
#      一个"响应对象"，形如 [{"content": "你"}, {"content": "好"}]（`resp`）
def stream_tokens(response):
    """模拟：从响应里一个个 yield 出 token"""
    for chunk in response:          # 每个 chunk 是一小段
        yield chunk["content"]

# 调用方：来一个打一个，不用等全部
for token in stream_tokens(resp):
    print(token, end="", flush=True)
```

FastAPI 的 `StreamingResponse`、很多框架的"流式回调"，底层都是 `yield`。
看到函数体里有 `yield`，你就知道：**这是在"边生产边消费"，不是攒齐了再给。**

### 2.4 生成器的好处

| | 普通函数 `return` | 生成器 `yield` |
|---|---|---|
| 内存 | 一次性装下所有结果 | **按需生产，省内存** |
| 拿到第一个值的时间 | 等全部算完 | **立刻拿到第一个**（对 TTFT 至关重要） |
| 能不能中途停 | 不能 | 能，`break` 就行 |

### 2.5 常见坑

| 坑 | 说明 |
|---|---|
| 调用含 `yield` 的函数后没反应 | 它只是创建了生成器对象，要用 `next()` 或 `for` 驱动 |
| 想两次遍历同一个生成器 | 生成器是**一次性**的，遍历完就空了。要重复遍历就重新调用函数 |
| 把生成器当列表用 `len(gen)` | 生成器没有 `len`，先 `list(gen)` 再算（但这会占内存） |

---

## 3. 类型注解：给代码"贴标签"

### 3.1 注解是什么、不是什么

第 04 章的函数你已经见过带箭头的写法：

```python
def calc_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    ...
```

- `model: str` 是**参数注解**（说"model 应该是字符串"）
- `-> float` 是**返回注解**（说"返回应该是小数"）

**最重要的认知（第 08 章反复强调过）**：这些注解**运行时完全不检查**。
`calc_cost("x", "1", "2")` 传错类型，**不会报错**。注解是给**人和编辑器**看的，不是给 Python 解释器看的。

> **前端对照**：Python 的注解 ≈ TS 的 `: string`，但**弱得多**——
> TS 会在编译期拦住你，Python 不会。它更像"文档的一部分"，而不是"类型的约束"。

### 3.2 那注解有什么用？

1. **编辑器提示**：IDE 能补全、能提示"你传的参数类型不对"
2. **文档作用**：别人看函数签名就知道传什么、返回什么
3. **配合 Pydantic / FastAPI 真正生效**：这些框架会在运行时**读取注解**并做校验

第 3 点是关键——**注解本身不管事，但框架会拿它来管事**。这就是为什么第 08 章的 Pydantic 要写注解：

```python
# 前置：这段需要下面这些前置（照抄时请一并复制）：
#      from pydantic import BaseModel（第 08 章讲过）
class AnalysisRequest(BaseModel):
    question: str          # Pydantic 读这个注解，才知道"这是必填字符串"
    max_rows: int = 1000
```

注解在这里**从"文档"变成了"校验规则"**。

### 3.3 常用写法速查

```python
from typing import Optional, Literal

def f1(name: str) -> str: ...                    # 基础类型
def f2(ids: list[int]) -> dict[str, float]: ... # 容器里装什么（Python 3.9+）
def f3(x: int | None) -> int: ...               # 可以是 int 或 None（3.10+）
def f4(x: Optional[int]) -> int: ...            # 上一行的旧写法（等价）
def f5(role: Literal["user", "assistant"]) -> None: ...  # 只能是这几个字面量
```

| 写法 | 意思 | 注意 |
|---|---|---|
| `list[int]` | 整数列表 | 3.9+ 才能直接写，旧版本要 `List[int]` |
| `dict[str, int]` | 键是 str、值是 int 的字典 | 同上 |
| `int \| None` | int 或 None | 3.10+；旧写法 `Optional[int]` |
| `Literal["a", "b"]` | 只能是 "a" 或 "b" | 第 08 章 Pydantic 用过 |

### 3.4 常见坑

| 坑 | 说明 |
|---|---|
| 以为注解会挡住错误类型 | **不会**。运行时传错类型照样跑，要校验靠 Pydantic |
| `Optional[int]` 和 `int \| None` 傻傻分不清 | 完全等价，前者是老写法，后者是新写法 |
| 以为 `int \| None` 就是"可选参数" | 不是。它只放宽**类型**，参数本身还是要传。真可选要加默认值 `= None` |

---

## 4. 三样东西怎么串起来

读一段 FastAPI 风格的代码，你应该能认出全部三样：

```python
from fastapi import FastAPI
from pydantic import BaseModel

class ChatRequest(BaseModel):              # 类型注解：定义数据结构
    question: str
    model: str = "deepseek-chat"

app = FastAPI()

@app.post("/chat")                          # 装饰器：把函数注册成路由
def chat(req: ChatRequest) -> dict:         # 类型注解：参数和返回值
    def generate():                         # 生成器：流式产出
        yield "你"
        yield "好"
        yield "！"
    return {"answer": "".join(generate())}
```

- `@app.post("/chat")` → **装饰器**，让 `chat` 变成 HTTP 接口
- `req: ChatRequest`、`-> dict` → **类型注解**，FastAPI 用它做参数校验
- `def generate(): yield ...` → **生成器**，换成真实实现就是"边生成边返回"

三样都能认出来，你就跨过了"读不懂框架代码"的门槛。

---

## 这一篇和哪几章有关

| 附录里的内容 | 对应的章节 |
|---|---|
| 函数、`*args` / `**kwargs` | [第 04 章](04-函数.md) |
| `with`、文件读写 | [第 06 章](06-异常与文件.md) |
| `@dataclass`、类、Pydantic | [第 08 章](08-类与对象.md) |
| 流式输出（生成器的实际用武之地） | [附录 A](附-A-读懂一次模型API调用.md)、[第 09 章](09-异步与并发.md) |
