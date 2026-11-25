# HTTP 基准数据（相对比较）

## 环境

WSL2 / Ubuntu，AMD Ryzen 7 7840H，16 逻辑核；客户端与服务端同机。

## keep-alive

`GET /`（约 1.35KB 响应），16 客户端：

- 单 IO 线程：约 9.5k QPS。
- 4 worker：约 23.5k QPS。

## Echo

64B payload，8 客户端：

- 单 IO 线程：约 37k QPS。
- 4 worker：约 34k QPS。

32 客户端：

- 单 IO 线程：约 61k QPS。
- 4 worker：约 74k QPS。

## 结论与限制

- Echo 场景计算量小，线程池开销可能大于收益。
- HTTP 解析 + 静态文件有实际工作，4 worker 提升约 2.5 倍。
- 同机压测只用于相对比较，不构成容量结论；完整评估需要独立客户端、预热与多次重复。
