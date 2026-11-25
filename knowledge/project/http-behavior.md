# HTTP 行为与安全边界

## 连接语义

- HTTP/1.1 默认 keep-alive；HTTP/1.0 默认关闭。
- `Connection: close` 覆盖为关闭；`Connection: keep-alive` 覆盖为保持。
- 错误响应（400/413）发送后关闭连接，避免继续解析不可信请求。

## keep-alive 与流水线

- 同一连接顺序发送请求 A、读取响应后发送请求 B，服务器复用连接。
- 同一连接一次写入多个请求时，服务器按解析循环依次处理并保持响应顺序。
- 每个连接持有一个有状态 parser；请求分多次到达时，状态必须跨回调保留。

## HEAD

- HEAD 只发送响应头，不发送 body。
- Content-Length 仍表示对应 GET 会发送的 body 长度。
- 响应发送逻辑用 `head_only` 参数控制 body 输出。

## 请求解析限额

- 请求行上限 8KB；Headers 累计上限 32KB；Body 按 Content-Length，上限 1MB，超限返回 413。
- 读缓冲上限 2MB、写缓冲上限 8MB；超限关闭慢连接。
- Content-Length 必须是严格十进制非负整数；重复且值不一致视为请求走私风险，直接拒绝。
- 不实现 chunked 请求体，遇到 `Transfer-Encoding: chunked` 明确拒绝。

## 静态文件与路径安全

- 只接受 GET / HEAD；其他方法返回 405。
- 处理顺序：URL 解码 → `weakly_canonical` 规范化 → 按路径组件检查仍在 `www_root` 内。
- 解码后出现 NUL 或非法百分号编码返回 400。
- 规范化后逃出 root 返回 403；目录补 `index.html`；非常规文件返回 404。
- 不使用字符串前缀判断根目录，避免 `/www-evil` 这类同前缀绕过。

## 缓存协商

- 响应携带 `ETag` 与 `Last-Modified`，`Cache-Control: no-cache`。
- `If-None-Match` 优先；没有它时才检查 `If-Modified-Since`。
- 命中返回 304，不带消息体。

## 响应头

- `Content-Length` 与 `Connection` 由 HttpResponse 统一生成，忽略调用方手动设置的同名头。
- 304 不发送 Content-Length。
- 响应头名大小写不敏感；同名覆盖但保留首次写入的写法。
