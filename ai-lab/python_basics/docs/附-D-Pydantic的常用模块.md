# Pydantic v2 

> Pydantic：基于Python类型注解做**运行时数据校验、类型自动转换、序列化**，常用于接口入参、JSON解析、配置解析（FastAPI大量使用）

## 一、核心模块与组件详解

### 1. BaseModel

**含义**：Pydantic 数据模型基类，定义数据结构的核心父类。 继承它写class，自动获得数据校验、类型自动转换、序列化、生成JSON Schema能力。 常用实例方法（v2）：

- `model_dump()`：转python字典（替代v1的`.dict()`）
- `model_dump_json()`：转json字符串（替代v1的`.json()`）
- `model_validate(data)`：校验原始字典并生成模型实例（替代v1 `parse_obj`）

```python
from pydantic import BaseModel

class User(BaseModel):
    id: int
    name: str

user = User.model_validate({"id": "100", "name": "张三"})
print(user.model_dump())
```

### 2. Field

**含义**：字段参数配置器，给模型字段附加校验规则、默认值、别名、文档、序列化控制。 支持：`gt`大于、`lt`小于、`max_length`最大长度、`default`默认值、`alias`别名、`exclude`序列化排除字段。

```python
from pydantic import BaseModel, Field

class User(BaseModel):
    age: int = Field(gt=0, le=120, description="年龄，0~120")
```

### 3. field_validator

**含义**：字段级自定义校验装饰器，对**单个字段**写自定义校验逻辑。

> v2 要求加上 `@classmethod`，可以抛出 ValueError 终止校验。

```python
from pydantic import BaseModel, field_validator

class User(BaseModel):
    name: str
    @field_validator("name")
    @classmethod
    def check_name(cls, v):
        if len(v) < 2:
            raise ValueError("名字太短")
        return v
```

### 4. model_validator

**含义**：模型级校验装饰器，可以**同时读取多个字段**做跨字段联合校验（比如密码和确认密码对比）。 模式：`mode="before"`（原始数据校验前） / `mode="after"`（字段校验完成后）

### 5. ValidationError

**含义**：校验失败抛出的异常类。捕获它拿到详细错误信息（字段、错误原因）。 解析JSON/接口入参时必捕获。

```python
from pydantic import BaseModel, ValidationError

try:
    User.model_validate({"id": "abc", "name": ""})
except ValidationError as e:
    print(e.errors())
```

### 6. TypeAdapter

**含义**：类型适配器，**不需要定义BaseModel**，直接对任意单一类型做校验、序列化。 适合简单类型、列表、TypedDict，快速校验零散数据。

```python
from pydantic import TypeAdapter

int_list_adapter = TypeAdapter(list[int])
res = int_list_adapter.validate_python(["1", "2", 3])
```

### 7. validate_call

**含义**：函数装饰器，**校验函数入参**。直接给函数加装饰器，自动校验调用参数类型。

```python
from pydantic import validate_call

@validate_call
def add(a: int, b: int):
    return a + b
```

### 8. 内置特殊类型（常用）

- `EmailStr`：邮箱格式校验（需要额外安装依赖 `pydantic[email]`）
- `HttpUrl`：http/https URL校验
- `SecretStr`：秘钥字符串，序列化自动隐藏内容，防止日志打印泄露密码

```python
from pydantic import BaseModel, EmailStr, SecretStr

class Login(BaseModel):
    email: EmailStr
    pwd: SecretStr
```

### 9. pydantic.dataclasses.dataclass

**含义**：pydantic版本dataclass装饰器。给标准python dataclass增加pydantic数据校验能力。

> 和BaseModel二选一；适合习惯dataclass写法的场景。

### 📌 选型口诀

- 结构化JSON/接口实体 → **BaseModel + Field**
- 单字段自定义校验 → **field_validator**
- 多字段关联校验 → **model_validator**
- 简单类型、列表，不想写class → **TypeAdapter**
- 校验函数参数 → **validate_call**
- 捕获校验报错 → **ValidationError**

### ❗ v1 vs v2 重要提醒

v1旧方法：`.dict()` / `.json()` / `@validator` / `parse_obj` v2新版：`model_dump()` / `model_dump_json()` / `@field_validator` / `model_validate()`

------

# Pydantic v2 极简速记卡（侧边笔记/快速查阅）

## ✅ 核心组件速记

| 组件             | 作用         | 一句话                                    |
| ---------------- | ------------ | ----------------------------------------- |
| BaseModel        | 模型基类     | 定义数据结构体，自动校验、序列化          |
| Field            | 字段配置     | 给字段加规则：范围、长度、别名、描述      |
| @field_validator | 单字段校验   | 单个字段自定义校验逻辑，加 `@classmethod` |
| @model_validator | 模型整体校验 | 多字段联合校验（如两次密码对比）          |
| ValidationError  | 校验异常     | 捕获校验失败，获取错误详情                |
| TypeAdapter      | 类型适配器   | 不用写BaseModel，直接校验list/dict等类型  |
| @validate_call   | 函数参数校验 | 装饰函数，自动校验函数入参                |
| EmailStr         | 邮箱         | 校验邮箱格式，需装额外依赖                |
| HttpUrl          | URL          | 校验http/https链接                        |
| SecretStr        | 保密字符串   | 密码/密钥，打印时自动隐藏                 |

## ✅ BaseModel 高频方法（v2）

```
model.model_dump()        # 对象 → dict（替代 .dict()）
model.model_dump_json()   # 对象 → json字符串（替代 .json()）
Model.model_validate(d)   # dict → 模型实例（替代 parse_obj）
```

## ✅ 选型口诀

- 接口/JSON结构体 → `BaseModel + Field`
- 单个字段自定义校验 → `@field_validator`
- 多个字段关联判断 → `@model_validator`
- 简单列表/字典，不想写类 → `TypeAdapter`
- 校验函数入参 → `@validate_call`
- 捕获校验报错 → `ValidationError`

## ✅ v1 → v2 迁移重点

- `.dict()` → `model_dump()`
- `.json()` → `model_dump_json()`
- `@validator` → `@field_validator`
- `parse_obj()` → `model_validate()`

## ✅ 最小示例（快速复制）

```python
from pydantic import BaseModel, Field

class User(BaseModel):
    id: int
    age: int = Field(gt=0, le=120)

u = User.model_validate({"id":1, "age":20})
print(u.model_dump())
```

> 捕获异常模板

```python
try:
    User.model_validate(data)
except ValidationError as e:
    print(e.errors())
```

# Python `isinstance`

## 作用

`isinstance(object, classinfo)`
**判断一个对象是不是某个（或某些）类型的实例，返回布尔值 `True/False`**

> ✅ 推荐优先用 `isinstance`，而不是 `type()` 判断类型。

## 语法

```
isinstance(对象, 类型)
# 支持多个类型：元组写法，满足其中一个就返回True
isinstance(对象, (类型1, 类型2))
```

## 基础示例

```
a = 10
print(isinstance(a, int))         # True
print(isinstance(a, str))         # False

b = "hello"
print(isinstance(b, (str, int)))  # True，是str或int其中一种即可
```

### 和 `type()` 的关键区别

- `type(obj)`：只严格匹配**本身类型**，**不考虑继承**
- `isinstance(obj, cls)`：**考虑继承关系**，子类实例也返回True

```python
class Animal:
    pass
class Dog(Animal):
    pass

d = Dog()
print(isinstance(d, Animal)) # True，Dog继承Animal
print(type(d) == Animal)      # False，type拿到的是Dog
```

## 搭配 Pydantic 的重点（你前面在学Pydantic）

> ⚠️ **不要用 `isinstance` 替代Pydantic校验！** `isinstance` 只能判断Python原生类型，**不会自动类型转换，不会校验业务规则**。

对比：

```python
data = {"age": "20"}

# isinstance 只能判断变量本身类型
age_str = data["age"]
print(isinstance(age_str, int)) # False，它是字符串"20"

# Pydantic：自动转类型 + 校验范围
from pydantic import BaseModel, Field
class User(BaseModel):
    age: int = Field(gt=0)
u = User.model_validate(data)
print(u.age, isinstance(u.age, int)) # 20 True，自动把字符串转int
```

### 小结

1. `isinstance`：**运行时判断对象类型，支持继承，只做类型判断，不做转换**
2. Pydantic：**带自动类型转换 + 业务规则校验（大小、长度、格式）**

## 常用场景

- 分支判断：拿到未知变量，判断是什么类型再处理

```python
def process(val):
    if isinstance(val, list):
        print("处理列表")
    elif isinstance(val, str):
        print("处理字符串")
```

- 类型断言，做简单的类型保护；复杂接口数据校验交给Pydantic

## 速记卡（可放进侧边笔记）

| 函数                 | 能力                     | 是否考虑继承 |
| -------------------- | ------------------------ | ------------ |
| isinstance(obj, cls) | 判断实例类型，多类型支持 | ✅ 是         |
| type(obj)            | 获取对象精确类型         | ❌ 不考虑继承 |

> 一句话：**简单类型判断用isinstance；带转换、业务规则、JSON数据校验，用Pydantic
