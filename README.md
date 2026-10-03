# astrbot_plugin_stream_quote · 流式引用回复

> 让 AstrBot（aiocqhttp / NapCat）在开启「流式输出」时，也能做到「引用回复」。

AstrBot 的「流式输出」与「回复时引用发送人消息」两个功能默认互斥。装上本插件后，机器人针对用户某一条消息产生的**第一条**流式输出会引用该用户消息，其余输出不引用。

## 特性

- 流式输出下，首条回复引用触发它的用户消息（OneBot v11 `reply` 段）。
- 群聊 / 私聊可分别开关。
- 可选跟随 AstrBot 主配置的「回复时引用发送人消息」开关。
- 幂等补丁：重复重载不叠加；禁用 / 卸载自动还原。
- 纯补丁实现，不修改 AstrBot 源码。

## 为什么默认不兼容

AstrBot 主配置里的「回复时引用发送人消息」(`platform_settings.reply_with_quote`) 是在 `ResultDecorateStage` / `RespondStage` 中，对最终消息链插入 `Reply(id=...)` 实现的。开启流式输出后结果类型变为 `STREAMING_RESULT`：

- `ResultDecorateStage` 因为 `result.chain` 为空直接 return，连 `on_decorating_result` 钩子都不会触发；
- `RespondStage` 直接调用 `event.send_streaming(result.async_stream, realtime_segmenting)`，由平台适配器自行缓冲 / 逐句拆分后再调用 `event.send(...)`。

也就是说，流式分支根本不经过插入 `Reply` 的那段逻辑 —— 这就是不兼容的根因。

## 安装

### 方式一：WebUI 上传（推荐）

1. 下载本仓库根目录下的 `astrbot_plugin_stream_quote.zip`（或自行打包插件目录，文件平铺在压缩包根目录即可）。
2. 打开 AstrBot WebUI → 「插件」→ 安装插件 → 上传文件，选择该 zip。
3. 安装后插件显示为「流式引用回复」（目录名 `astrbot_plugin_stream_quote`）。

### 方式二：手动放置

把插件目录放入 AstrBot 的插件目录，然后重启或在插件页「重载插件」：

- 打包版（AstrBot Launcher 默认数据根）：`~/.astrbot/data/plugins/`
- 源码 / 多实例版：`<实例>/core/data/plugins/`

## 配置

在 WebUI 插件配置面板调整，或编辑 `data/config/astrbot_plugin_stream_quote_config.json`。

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `enable` | bool | `true` | 插件总开关，关闭后等同于未安装 |
| `quote_in_group` | bool | `true` | 群聊中引用用户消息 |
| `quote_in_private` | bool | `true` | 私聊中引用用户消息 |
| `respect_reply_with_quote` | bool | `true` | 跟随主配置的「回复时引用发送人消息」；关闭后只要插件启用就强制引用 |
| `debug` | bool | `false` | 输出调试日志 |

## 使用

1. 在 AstrBot 主配置里打开「流式输出」。
2. 确保主配置的「回复时引用发送人消息」为开；或在插件里把 `respect_reply_with_quote` 关掉以强制启用。
3. 机器人流式回复时，第一条消息会引用用户消息，后续消息不再引用。
4. 在聊天中发送 `/streamquote`（别名 `/流式引用`）可查看插件当前状态。

## 兼容性

- 目标平台：`aiocqhttp`（OneBot v11）+ NapCat。
- 已在 AstrBot **4.24.5 ~ 4.28.2** 实测可用。
- 仅影响 aiocqhttp 的流式发送路径，不改动其它平台，也不影响非流式行为。

## 实现原理

插件加载时给 `AiocqhttpMessageEvent.send_streaming` 打一个**幂等**补丁：

1. 在该次流式发送执行期间，临时包装**同一个事件对象**的 `send` 方法；
2. 只对本次流式发送中**最先**发出的那条消息，在消息链头部插入 `Reply(id=用户消息ID)`（OneBot v11 的 `reply` 段）；
3. 之后的消息以及 `finally` 恢复后不受影响。

补丁是幂等的：重载插件不会叠加包装；插件被禁用 / 卸载时通过 `terminate()` 还原原始方法。

## 已知限制与声明

- 本插件通过 monkey-patch 修改了 AstrBot 的 `AiocqhttpMessageEvent.send_streaming` 行为。**AstrBot 升级若改动该方法，可能需要适配本插件**，请留意兼容性说明。
- 仅对「第一条」流式消息注入引用，这是设计行为而非缺陷。
- 仅支持 aiocqhttp 平台。

## 常见问题

- 上传安装提示「目录已存在」：说明之前已装过同名目录，先在插件页卸载或手动删除 `data/plugins/astrbot_plugin_stream_quote`，再重新上传。
- 装了没效果：确认主配置「流式输出」已开启、插件 `enable` 为开，并发送 `/streamquote` 查看状态。
- 不想触发引用时：关闭 `enable`，或把对应的 `quote_in_group` / `quote_in_private` 关掉。

## 目录结构

```text
astrbot_plugin_stream_quote/
├── main.py             # 插件主体与补丁逻辑
├── metadata.yaml       # 插件元数据
├── _conf_schema.json   # 配置 Schema
└── README.md
```

## License

[MIT](./LICENSE) © 2026 lousha114514
