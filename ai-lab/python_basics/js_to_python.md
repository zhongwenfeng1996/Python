# JS/TS → Python 速查表（AI 应用方向）

> 用法：花 30 分钟从头读一遍，不要背。之后写代码时回来查。
> 只覆盖你在 LLM 应用开发里**马上会用到**的部分，不追求语言完整性。

---

## 1. 变量

```javascript
// JavaScript
let count = 0;
const name = "Alice";
```

```python
# Python —— 没有 let / const，直接写名字
count = 0
name = "Alice"
NAME = "Alice"     # 全大写是"常量"的约定，语言层面不强制
```

**注意**：Python 没有 `const`。不要改全大写的变量，这是纪律问题，不是语法问题。

---

## 2. 字符串

| 需求 | JavaScript | Python |
|---|---|---|
| 模板字符串 | `` `你好 ${name}` `` | `f"你好 {name}"` |
| 拼接 | `a + b` | `a + b` |
| 长度 | `s.length` | `len(s)` |
| 包含 | `s.includes("x")` | `"x" in s` |
| 分割 | `s.split(",")` | `s.split(",")` |
| 替换 | `s.replace(/a/g, "b")` | `s.replace("a", "b")` |
| 去空白 | `s.trim()` | `s.strip()` |
| 转大小写 | `s.toUpperCase()` | `s.upper()` |

```python
name = "小明"
age = 25
print(f"{name} 今年 {age} 岁")     # f 开头，{} 里放变量
print(f"{age=}")                   # 调试神器，输出 age=25
```

**关键差异**：JS 的字符串是对象，可以 `.length`；Python 的字符串要靠 `len()` 这种全局函数。
**为什么**：Python 里"长度"是通用操作，能作用于字符串、列表、字典，所以做成了函数。

---

## 3. 数组 vs 列表

```javascript
const list = [1, 2, 3];
list.push(4);              // 追加
list.map(x => x * 2);      // 映射
list.filter(x => x > 2);   // 过滤
list.length;
list[0];
list.slice(1, 3);
```

```python
items = [1, 2, 3]
items.append(4)                       # 追加（注意：不是 push）
[x * 2 for x in items]                # 列表推导式 ← Python 最爱的写法
[x for x in items if x > 2]
len(items)
items[0]
items[1:3]                            # 切片（含头不含尾）
```

**列表推导式**是前端最需要适应的写法：

```python
# JS:  const names = users.map(u => u.name)
names = [u["name"] for u in users]

# JS:  const adults = users.filter(u => u.age >= 18).map(u => u.name)
adults = [u["name"] for u in users if u["age"] >= 18]
```

---

## 4. 对象 vs 字典

```javascript
const user = { name: "Alice", age: 25 };   // key 不用引号
user.name;
user["name"];
user.email ?? "未知";
```

```python
user = {"name": "Alice", "age": 25}        # key 必须加引号
user["name"]                                   # 只能用中括号，没有点号
user.get("email", "未知")                       # 安全取值，不存在返回默认值
```

**这是前端最容易踩的坑**：Python 字典**不能用点号取值**。`user.name` 会报错。
（除非用 Pydantic 模型或 dataclass，那是后面 W2 的内容。）

---

## 5. 函数

```javascript
function add(a, b = 1) {
  return a + b;
}

const add2 = (a, b) => a + b;
```

```python
def add(a, b=1):
    return a + b

# 没有箭头函数，小逻辑用 lambda（少用，可读性差）
add2 = lambda a, b: a + b
```

**Python 独有的坑**：默认参数只求值一次。

```python
def bad(items=[]):        # 错误！所有调用共享同一个 list
    items.append(1)
    return items

def good(items=None):     # 正确
    if items is None:
        items = []
    items.append(1)
    return items
```

---

## 6. 条件与循环

| 需求 | JavaScript | Python |
|---|---|---|
| 且 / 或 / 非 | `&&` / `\|\|` / `!` | `and` / `or` / `not` |
| 空值判断 | `x === null` | `x is None` |
| 三元 | `a ? b : c` | `b if a else c` |
| 遍历数组 | `for (const x of list)` | `for x in list:` |
| 带下标遍历 | `list.forEach((x, i) => ...)` | `for i, x in enumerate(list):` |
| 固定次数 | `for (let i = 0; i < 3; i++)` | `for i in range(3):` |

```python
if score >= 90 and not is_absent:
    print("优秀")
elif score >= 60:              # 不是 else if
    print("及格")
else:
    print("不及格")
```

**缩进就是语法**。JS 用 `{}` 划块，Python 用缩进（4 个空格）。缩进错了程序就跑不对。

---

## 7. 空值

```javascript
const x = null;
const y = undefined;
x?.name;            // 可选链
value ?? "default"; // 空值合并
```

```python
x = None                     # Python 只有 None 一个空值（没有 undefined）
# 没有 ?. 可选链，写成：
name = x["name"] if x else None
# 或者用字典的 get：
name = data.get("name", "default")
```

---

## 8. 异常处理

```javascript
try {
  risky();
} catch (e) {
  console.error(e.message);
} finally {
  cleanup();
}
```

```python
try:
    risky()
except Exception as e:          # 是 except，不是 catch
    print(f"出错了: {e}")
finally:
    cleanup()
```

---

## 9. 模块与导入

```javascript
import fs from "fs";
import { readFile } from "fs/promises";
```

```python
import json                        # 整个模块
from pathlib import Path           # 只导入某个名字
from datetime import datetime, timedelta
```

**注意**：Python 的模块名是**文件路径**。`from utils.helpers import clean` 对应 `utils/helpers.py` 里的 `clean`。

---

## 10. 包管理对照

| 概念 | JavaScript | Python |
|---|---|---|
| 依赖清单 | `package.json` | `pyproject.toml` / `requirements.txt` |
| 安装依赖 | `npm install` | `pip install` / `uv add` |
| 隔离环境 | `node_modules/`（自动） | **venv（必须手动创建和激活）** |
| 锁文件 | `package-lock.json` | `uv.lock` / `poetry.lock` |
| 运行脚本 | `npm run dev` | `python main.py` |

**前端最容易踩的坑**：Python 默认把包装到**全局**。
每个项目都应该先建虚拟环境：

```bash
python3 -m venv .venv          # 创建
source .venv/bin/activate      # 激活（macOS/Linux）
pip install httpx              # 装到这个项目里
```

> 本次给你的脚本都是**零依赖**的（只用标准库），所以暂时不用管虚拟环境。
> 等你装第一个第三方库时再回来做这一步。

---

## 11. 常用操作对照表

| 需求 | JavaScript | Python |
|---|---|---|
| 打印 | `console.log(x)` | `print(x)` |
| 长度 | `x.length` | `len(x)` |
| 类型 | `typeof x` | `type(x)` |
| 转整数 | `parseInt(s)` | `int(s)` |
| 转字符串 | `String(x)` | `str(x)` |
| JSON 解析 | `JSON.parse(s)` | `json.loads(s)` |
| JSON 序列化 | `JSON.stringify(o)` | `json.dumps(o)` |
| 读环境变量 | `process.env.KEY` | `os.environ.get("KEY")` |
| 发请求 | `fetch(url)` | `urllib.request.urlopen(...)` |
| 排序 | `arr.sort((a,b)=>a-b)` | `sorted(arr)` |
| 去重 | `[...new Set(arr)]` | `list(set(arr))` |
| 求和 | `arr.reduce((a,b)=>a+b, 0)` | `sum(arr)` |
| 最大 / 最小 | `Math.max(...arr)` | `max(arr)` / `min(arr)` |
| 枚举 | `Object.entries(o)` | `o.items()` |
| 时间 | `Date.now()` | `datetime.now()` |
| 主入口 | 直接执行 | `if __name__ == "__main__":` |

---

## 12. 你需要暂时忽略的东西

这些东西 Python 里很常见，但**现在不用学**：

- 类（`class`）与继承 —— 等你用 Pydantic / FastAPI 时再学
- 装饰器（`@xxx`）—— W2 学 FastAPI 时会遇到
- 生成器 / `yield` —— 暂时用列表就够
- `asyncio` —— W1 尾声再碰，先把同步版跑通
- 类型注解 —— 看懂即可，运行时它不检查任何东西

> **判断标准**：能不能看懂 `python_basics/` 下的三个脚本，并自己改出一个小功能。
> 能，就进入正课；不能，再多写几个小脚本。
