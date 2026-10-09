# 第二层 · 后端篇：写出能上线的 LLM 后端服务

> **这一层和第一层的区别**：
> 第一层（`docs/`）的目标是"**读懂** AI 应用的代码"；
> 这一层是"**自己写出**一个后端服务"——路由、校验、流式、测试，一样不少。

---

## 前置要求（硬门槛，不满足会看不懂）

**必须先读完第一层 `docs/` 的 01–10 章。** 这一层假设你已经会：

| 你需要会的 | 来自 |
|---|---|
| 变量、类型、字典、列表、推导式 | 01–03 章 |
| 函数、参数、返回值、作用域 | 04 章 |
| 条件、循环、`break`/`continue` | 05 章 |
| `try`/`except`、自定义异常、文件读写 | 06 章 |
| `import`、`pip`、虚拟环境 | 07 章 |
| 类、`@dataclass`、Pydantic 模型（够用版） | 08 章 |
| `async`/`await`、`asyncio.gather` | 09 章 |
| 读 traceback、断点排查 | 10 章 |

更完整地读懂 API 响应结构，看 [附录 A](../docs/附-A-读懂一次模型API调用.md)。

> **这一层内部也遵守"零前置"**：第 N 章只用第 1..N-1 章和上面的前置要求教过的东西。
> 用 `check_doc_prerequisites.py` 校验（已扩展到本目录）。

---

## 章节

| 章 | 主题 | 示例文件 | 学完能做什么 |
|---|---|---|---|
| [01](#01--pydantic-基础) | Pydantic 基础 | [`demos/01_pydantic_basics.py`](demos/01_pydantic_basics.py) | 用类型注解定义数据结构并自动校验 |
| [02](#02--pydantic-进阶) | Pydantic 进阶 | [`demos/02_pydantic_validate.py`](demos/02_pydantic_validate.py) | 自定义校验、生成工具 schema、字段别名 |
| [03](#03--自定义异常) | 自定义异常 | [`demos/03_custom_exceptions.py`](demos/03_custom_exceptions.py) | 让"调用失败"能分类型处理 |
| [04](#04--fastapi-最小服务) | FastAPI 最小服务 | [`demos/04_fastapi_minimal.py`](demos/04_fastapi_minimal.py) | 写出带校验的 HTTP 接口 |
| [05](#05--流式响应sse) | 流式响应（SSE） | [`demos/05_streaming_sse.py`](demos/05_streaming_sse.py) | 实现"打字机"效果的流式接口 |
| [06](#06--测试接口) | 测试接口 | [`demos/06_testing_api.py`](demos/06_testing_api.py) | 用 pytest 给接口写测试 |

**每章都有可运行示例**，不是伪代码。跑一遍比读三遍有用：

```powershell
$py = "G:\转型\.venv\Scripts\python.exe"
cd G:\转型\ai-lab\python_basics\backend\demos

& $py 01_pydantic_basics.py        # 直接运行，打印每步结果
& $py 02_pydantic_validate.py
& $py 03_custom_exceptions.py
& $py 04_fastapi_minimal.py        # 也可以起真服务看接口文档：
                                   #   & $py -m uvicorn 04_fastapi_minimal:app --port 8000
                                   #   浏览器打开 http://127.0.0.1:8000/docs
& $py 05_streaming_sse.py
& $py -m pytest 06_testing_api.py -v
```

一键跑全部（含结果校验）：

```powershell
& $py verify_backend_demos.py
```

---

## 为什么是这六章

不是"FastAPI 有什么就教什么"，而是**项目一真实用到什么就教什么**。
教材里的每段代码都能在 [`projects/project1-stream-chat/backend/`](../../projects/project1-stream-chat/backend/)
找到对应实现：

| 这一层教的 | 项目一里的真实文件 |
|---|---|
| Pydantic 模型、校验 | `config.py`（`ModelSpec`/`Settings`）、`cost.py`（`Usage`） |
| 自定义异常分类型 | `providers.py`（`UpstreamError`/`FirstTokenTimeout`） |
| FastAPI 路由、请求响应 | `main.py`（`/api/models`、`/api/chat`） |
| 流式响应 + 取消 | `main.py`（`_relay`）、`providers.py`（`_stream_once`） |
| 测试接口 | `tests/test_api.py`（13 条）、`tests/conftest.py` |

**顺序也是有理由的**（不是按库的文档目录）：

```
01 Pydantic 基础 ──┐
                   ├─→ 04 FastAPI ──→ 05 流式 ──→ 06 测试
02 Pydantic 进阶 ──┤        ↑
03 自定义异常 ─────┘        │
        异常要排在 FastAPI 前：路由里要抛/接异常
```

---

## 01 · Pydantic 基础

**解决什么问题**：数据从外部进来（HTTP 请求、模型输出）时**不可信**，
必须校验；手写校验很快就会失控。

**核心收获**：类型注解就是校验规则；失败时给的是**结构化数据**（哪个字段、错在哪），
不是一大段字符串。

```python
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
```

---

## 02 · Pydantic 进阶

**只讲 LLM 应用真的会用到的四件事**（不讲"Pydantic 还有什么功能"）：

1. `field_validator` —— 校验 + 归一化（模型返回的温度 2.5、前后带空格的输入）
2. `model_json_schema()` —— **这就是 function calling 的 `parameters`**
3. 校验失败 -> 拼成简短提示喂回模型，让它自我修正（而不是把一大段报错塞进去）
4. 别名字段 —— 前端 `camelCase` 与后端 `snake_case` 的桥

> 第 2 条是这一层最值钱的一节：**工具调用不是玄学，就是把 Pydantic schema
> 塞进请求体的 `tools[].function.parameters`。**

---

## 03 · 自定义异常

**解决什么问题**：`raise RuntimeError("调用失败")` 的信息量太低。
调用方需要知道"该重试、该改配置、还是该降级"。

**核心收获**：靠 `isinstance(exc)` 分类型处理，而不是 `if "401" in str(exc)` 靠字符串猜。

四类真实失败的处理方式完全不同：

| 失败 | 该怎么办 |
|---|---|
| 网络连不上 | 可以重试 |
| 401 / 403 | key 配错了，**重试无用**，直接报错 |
| 429 限流 | 退避后重试 |
| 首 token 超时 | 换备用模型**降级** |

---

## 04 · FastAPI 最小服务

**核心认知**：FastAPI 的本质是"**把类型注解变成运行时行为**"。

```python
@app.get("/models")
async def list_models(limit: int = 3) -> dict:   # 函数签名 = 接口契约
    ...
```

- 参数类型 -> 自动解析 + 校验（错了返回 422，不用自己写）
- 返回类型 -> 自动序列化 + 生成接口文档（`/docs` 白送）
- `async def` -> 支持并发处理请求

示例里同时演示了**项目一的写法**（`await request.json()` 手动解析），
对比之后你会明白为什么用 Pydantic 更好。

---

## 05 · 流式响应（SSE）

**这是 LLM 应用最核心的形态** —— 所有"打字机效果"都是它。

三件套：

```
服务端： async def + yield   ->  StreamingResponse
客户端： client.stream() + aiter_lines()  ->  逐段渲染
格式：   event: / data: + 空行结尾
```

**最容易踩的坑**：SSE 每条消息必须以**空行**结尾。少了它，
浏览器会一直等下一行 —— 表现为"服务端明明发了，前端收不到"。

**为什么必须流式**：总耗时差不多，但首字延迟（TTFT）差 10 倍。
用户盯着空白屏幕 100ms 和 10ms 就看到字，是完全不同的体感。

---

## 06 · 测试接口

**为什么值得单独一章**：接口测试是最容易写、回报最高的一类测试。

三个要点：

1. **夹具**（fixture）—— `create_app()` 每次返回全新 app，
   避免测试之间互相污染（这类 bug 极难查）
2. **参数化** —— 三组非法输入写成数据，而不是复制三遍测试函数
3. **测流式不能只看状态码** —— 流式最容易坏的就是内容，
   而那时候状态码还是 200

> **两种测试方式的取舍**（项目一的 `conftest.py` 里写了）：
> `ASGITransport`（进程内）快、不占端口，但**不经过真实 socket**，
> 所以"客户端断开后上游有没有停"这类测试测不了 —— 那种必须起真进程。

---

## 这一层**没有**覆盖的（诚实清单）

按项目一的真实代码为准，以下内容这一层暂时没讲。它们不影响你读懂并改动项目一，
但如果你想从零写一个更复杂的后端，会遇到：

| 没覆盖 | 什么时候会遇到 |
|---|---|
| `Depends` 依赖注入 | 多个接口共享数据库连接/配置时 |
| `lifespan` 生命周期 | 在启动时建连接池、关闭时释放 |
| 中间件（除 CORS 外） | 统一日志、请求 ID、鉴权 |
| 分层与项目结构 | 代码超过 500 行，需要 service/repository 分层 |
| 环境变量的工程化做法 | `pydantic-settings`、多环境配置 |
| 数据库/ORM | 项目三（数据 Agent）会需要 |
| 并发控制、限流、重试退避 | 真实流量上来之后 |
| 部署（Docker / 进程管理） | 上线时 |

**要不要现在补**：不必。这份清单更像"知道边界在哪"——
免得你以为学完就什么都懂了。真正需要的时候再来查。

---

## 和第一层的目录关系

```
ai-lab/python_basics/
├── docs/                    ← 第一层 基础篇（读懂代码）
│   ├── 01..10 章
│   └── 附-A-读懂一次模型API调用.md
└── backend/                 ← 第二层 后端篇（写出服务）
    ├── README.md            ← 本文件
    ├── pytest.ini
    ├── verify_backend_demos.py
    └── demos/
        ├── 01_pydantic_basics.py
        ├── 02_pydantic_validate.py
        ├── 03_custom_exceptions.py
        ├── 04_fastapi_minimal.py
        ├── 05_streaming_sse.py
        └── 06_testing_api.py
```
