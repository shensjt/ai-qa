# C++ 项目的构建与测试工具

## 编译

```bash
cmake -S . -B build/server -DCMAKE_BUILD_TYPE=Release -DBUILD_COMMIT=$(git rev-parse --short HEAD)
cmake --build build/server -j
```

产物：`build/server/web_server`、`build/server/kv_store`。

## 单元测试与集成测试

```bash
cmake -S tests -B build/tests
cmake --build build/tests -j
ctest --test-dir build/tests --output-on-failure
```

测试覆盖线程池、内存池、Buffer、Epoll、TcpServer/TcpConnection、HTTP 解析/响应/静态文件/LRU、
异步日志，以及 KV 的跳表、RESP、AOF 和服务集成。

## Sanitizer

- ASan/UBSan：`-DENABLE_ASAN=ON`
- TSan：`-DENABLE_TSAN=ON`

分别构建到独立目录，运行 ctest。

## 静态站点构建

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r blog/requirements.txt
make blog   # 生成 build/www
```

MkDocs 使用 `blog/mkdocs-demo.yml`（演示内容）或 `blog/mkdocs.yml`（本地内容）。

## wrk

```bash
wrk -t4 -c50 -d10s http://127.0.0.1:8080/
```

- `-t` 线程、`-c` 连接、`-d` 持续时间。
- 与服务端同机压测时数字只用于相对比较，不能代表独立客户端环境。
- 需要配合 pidstat 等工具看 CPU/内存；单次结果不足以判定容量或泄漏。
