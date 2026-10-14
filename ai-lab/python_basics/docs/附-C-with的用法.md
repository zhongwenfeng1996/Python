# Python with 上下文管理器：适用场景 + 正反示例

> 核心判断：**凡是需要申请资源，用完必须 close/release，且代码中间可能抛出异常导致清理代码无法执行，就适合使用 `with`。`with` 本质是 `try-finally` 的语法糖，离开代码块（无论正常结束还是报错）都会自动执行资源清理。**

## ✅ 适合使用 with 的场景与示例

### 1. 本地文件读写（最常用）

```python
# 读取文本文件
with open("data.json", "r", encoding="utf-8") as f:
    text = f.read()

# 写入文本文件
with open("output.txt", "w", encoding="utf-8") as f:
    f.write("hello python")

# 二进制写入（保存图片）
with open("img.jpg", "wb") as f:
    f.write(binary_data)
```

### 2. 网络IO（urllib 网络请求，你之前的例子）

```python
import urllib.request

req = urllib.request.Request("https://httpbin.org/get")
with urllib.request.urlopen(req, timeout=60) as resp:
    body = resp.read().decode("utf-8")
```

### 3. requests Session 会话管理

```python
import requests
with requests.Session() as s:
    res = s.get("https://httpbin.org/get")
```

### 4. SQLite 数据库连接

```python
import sqlite3
with sqlite3.connect("test.db") as conn:
    # 正常退出自动commit；发生异常自动rollback
    cur = conn.cursor()
    cur.execute("INSERT INTO t(name) VALUES (?)", ("abc",))
# 离开with块自动关闭数据库连接
```

### 5. 多线程锁，自动加锁/释放锁

```python
import threading
lock = threading.Lock()

with lock:
    # 进入代码块：自动 acquire() 加锁
    print("临界区代码")
    # 离开代码块：自动 release() 释放锁，防止死锁
```

### 6. 压缩包 zipfile

```python
import zipfile
with zipfile.ZipFile("archive.zip", "r") as zf:
    print(zf.namelist())
```

### 7. 自定义上下文管理器

```python
class MyResource:
    def __enter__(self):
        print("打开资源")
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        print("关闭资源")
        return False

with MyResource() as r:
    print("使用资源")
```

## ❌ 不使用 with 的反面示例（资源泄漏演示）

> 下面代码：中间触发异常，`f.close()` 不会执行，文件句柄泄漏，文件被持续占用。

```
# 不推荐写法：手动管理文件
f = open("test.txt", "r", encoding="utf-8")
# 模拟中间抛出异常
1 / 0
f.close()
```

## 📌 with 等价 try-finally 底层对照

```python
# with写法
with open("a.txt","r") as f:
    data = f.read()

# 等价原生 try-finally
f = open("a.txt","r")
try:
    data = f.read()
finally:
    f.close()
```

## ❌ 不需要使用 with 的场景

普通基础类型（字符串、int、list、dict）没有需要释放的资源，不能放入 `with`：

```python
# 错误示例，会直接报错
with "hello" as s:
    pass
```

## 📖 判断口诀

> 需要 open / connect / acquire，用完必须 close / disconnect / release，代码有可能抛异常 → 用 with。

