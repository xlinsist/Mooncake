

# 6. 阶段三：实现透明异构存储层

这才是老师现在比较认可的系统方向。

第一版本不要真的做完整 POSIX 文件系统。

先做一个最小的 logical storage layer。

---

## 系统结构

```text
              LLM / Benchmark
                     |
                     v
            +----------------+
            | KV Storage API |
            +-------+--------+
                    |
            +-------v--------+
            | Metadata Layer |
            +-------+--------+
                    |
            +-------v--------+
            | Policy Manager |
            +---+---------+--+
                |         |
                v         v
           Local NVMe   NVMe-oF
```

---

## 6.1 Storage API

第一版只需要：

```cpp
put(key, buffer, size)
get(key, buffer)
remove(key)
```

暂时不要做复杂 filesystem semantics。

---

## 6.2 Metadata Manager

维护：

```text
KV ID
size
location
device
offset
stripe width
state
```

例如：

```text
req_1001
size = 8 GB
location = remote
devices = SSD2, SSD3
stripe = 2
```

上层不需要知道这些信息。

---

## 6.3 Backend

只保留两个：

```text
LocalNvmeBackend

MooncakeNoFBackend
```

后续再增加：

```text
DRAMBackend
```

---

## 6.4 Policy Interface

定义：

```text
select_write_target()

select_read_source()

migrate()

get_device_state()
```

第一版甚至可以只有：

```text
LOCAL_ONLY
REMOTE_ONLY
ROUND_ROBIN
```

这里重点不是策略聪明，而是把：

> **机制和策略分离。**

这对后面的系统论文设计很重要。

---

# 7. 阶段四：验证透明层本身没有明显开销

这是老师提出“文件系统封装”以后必须有的一组实验。

比较：

```text
Direct Local NVMe
vs
Storage Layer → Local NVMe
```

以及：

```text
Direct Mooncake NoF
vs
Storage Layer → Mooncake NoF
```

测：

* latency overhead；
* bandwidth loss；
* CPU overhead。

目标是能够说明：

> 使用透明抽象带来的额外软件开销很小，而换来了统一资源管理能力。
