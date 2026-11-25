# WebServer 运行契约

## 启动命令

```bash
./build/server/web_server --host 127.0.0.1 --port 8080 \
  --root build/www --threads 4 --timeout 60 --cache-bytes 16777216
```

## 启动参数

- `--host`：监听地址，默认 `127.0.0.1`。
- `--port`：监听端口，默认 `8080`。
- `--root`：静态站点根目录，默认 `build/www`；目录不存在时启动失败。
- `--threads`：worker 线程数，默认 `4`。
- `--timeout`：空闲连接超时秒数，默认 `60`。
- `--cache-bytes`：LRU 文件缓存上限，默认 `16777216`（16MB）。

未知参数打印 usage 并以非零码退出。编译时可通过 `-DBUILD_COMMIT=<sha>` 注入 commit。

## 健康检查

`GET /healthz` 返回：

```json
{"status":"ok","commit":"<BUILD_COMMIT>"}
```

- Content-Type 为 `application/json; charset=utf-8`，`Cache-Control: no-store`。
- `HEAD /healthz` 返回相同头部但不发送 body。
- 其他方法返回 `405 Method Not Allowed`，并带 `Allow: GET, HEAD`。

## 日志

- 默认写 `logs/server.log`（相对进程工作目录），启动失败时回退到 stderr。
- 访问日志包含 method、path、status、bytes、duration_ms。

## 停机

- 主线程屏蔽 `SIGINT` / `SIGTERM`，在 `sigtimedwait` 中消费。
- 收到信号后调用 `Stop()`：停止 accept、等待 worker 退出、刷新异步日志。
- 不使用“杀掉占用端口的其他进程”的清理方式。

## 目录约定

- 源码与文档在仓库内，`build/` 是构建产物，不入库。
- 静态站点由 MkDocs 生成到 `build/www`；干净 clone 后需先构建博客，否则没有可发内容。
