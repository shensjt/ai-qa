# RFC 9112 消息分帧摘要

> 本文是 RFC 9112（HTTP/1.1）的检索用摘要，不是标准原文；完整定义以
> https://www.rfc-editor.org/rfc/rfc9112 为准。

## 消息结构

- HTTP/1.1 消息由起始行、Header 字段、空行和可选消息体组成。
- 请求行包含 method、request-target 和 HTTP-version，字段之间以单个空格分隔。
- Header 字段名为 token，大小写不敏感；字段值可包含可见字符、空格和水平制表符。

## 消息体长度

- 有 `Content-Length` 时，按字节数确定消息体边界。
- 有 `Transfer-Encoding` 且最终编码为 chunked 时，按 chunk 序列确定边界。
- 两者同时出现、或 Content-Length 值不合法，属于消息分帧歧义，接收方必须按错误处理，防止请求走私。
- 没有 Content-Length 且不是 chunked 时，响应可由连接关闭定界，但请求不应依赖这种形式。

## 持久连接

- HTTP/1.1 默认持久连接，除非发送 `Connection: close`。
- 每个消息后连接保持打开，接收方必须正确处理一个连接上的下一个消息。
- 消息边界必须严格按 Content-Length 或 chunked 计算，不能依赖一次 read 的边界。

## HEAD 与状态码

- HEAD 的响应不得包含消息体；但 Header 应与其对应 GET 一致，包括 Content-Length。
- 1xx、204、304 响应不带消息体。

## 流水线

- 客户端可以在收到响应前连续发送多个请求；服务器必须按请求顺序返回响应。
- 服务器仍需逐条解析请求，不能把多个请求的字节混在一起处理。

## 错误处理

- 无法解析的请求行、Header 或分帧应返回 4xx；服务器可以关闭连接以避免继续错位解析。
- 对超长起始行、Header 和消息体应设置实现上限。
