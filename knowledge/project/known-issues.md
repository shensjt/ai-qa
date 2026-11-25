# 已知问题：分片请求返回 405

## 现象

在 2026-11-25 的自动化测试中，请求在“请求行之后”被拆成多个 TCP 片段发送时，服务器返回
`405 Method Not Allowed`，访问日志显示 `method=UNKNOWN path=`。

## 复现矩阵

- 全部一次发送：200。
- 在请求行内部切分，第二次到达才补全请求行：200。
- 请求行完成后切分 Header：405。
- Header 中间切分：405。
- 最后一个 CRLF 之前切分：405。

## 根因

`HttpParser` 跨 `Parse()` 调用只保存状态机与长度，不保存已解析的 method、target 和 headers；
`HttpServer::OnMessage` 每次回调都新建 `HttpRequest`。当请求行与后续 Header 分属不同回调时，
后半包解析完成返回的是默认空请求，静态文件处理器因此按未知方法返回 405。

## 影响

- 分片/半包解析不正确，违反了“同一连接可增量解析”的设计目标。
- 分片到达的 `Connection: close`、`If-None-Match` 等 Header 也可能丢失。
- 对应自测用例 `http.split_request` 会判 FAIL。

## 建议修复方向

让 `HttpParser` 持有当前请求对象，跨调用累积，完成时拷出，`Reset()` 时清空；
并补充“在请求行之后任意字节位置切分”的单元测试。

## 复现方式

使用原始 socket 发送合法 GET 请求，先发送完整请求行，等待数毫秒，再发送剩余 Header 与空行。
