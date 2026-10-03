"""astrbot_plugin_stream_quote

让 AstrBot（aiocqhttp / NapCat）在开启「流式输出」时，也能做到「引用回复」：

    机器人针对用户某一条消息产生的**第一条**输出会引用该用户消息，
    其余流式输出不引用。

为什么需要本插件
----------------
AstrBot 的「回复时引用发送人消息」(platform_settings.reply_with_quote) 是在
ResultDecorateStage / RespondStage 里，对 `event.get_result().chain` 头部插入
`Reply(id=...)` 实现的。

但开启流式输出后，结果类型是 `ResultContentType.STREAMING_RESULT`：
- ResultDecorateStage 因为 `result.chain` 为空直接 return（钩子都不会触发）；
- RespondStage 直接调用 `event.send_streaming(result.async_stream, realtime_segmenting)`，
  由平台适配器自己缓冲 / 逐句拆分后再调用 `event.send(...)`。

也就是说，流式分支根本不会经过那段插入 `Reply` 的逻辑 —— 这就是
「流式输出」和「引用回复」不兼容的根因。

本插件的做法
------------
插件加载时，给 `AiocqhttpMessageEvent.send_streaming` 打一个轻量补丁：
在该次流式发送执行期间，临时包装**同一个事件对象**的 `send` 方法；
只对本次流式发送中**最先**发出的那条消息，在消息链头部插入一个
`Reply(id=用户消息ID)`（OneBot v11 的 reply 段），随后立即恢复，其余消息不受影响。

补丁是幂等的：重复加载 / 重载插件不会叠加包装；插件被禁用或卸载时会还原原始方法。
"""

from astrbot.api import logger

try:  # 组件导出位置在不同版本略有差异，做个兜底
    from astrbot.api.message_components import Plain, Reply
except Exception:  # pragma: no cover
    from astrbot.core.message.components import Plain, Reply  # type: ignore

try:
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.star import Context, Star
except Exception:  # pragma: no cover
    AstrMessageEvent = None  # type: ignore
    Context = object  # type: ignore
    Star = object  # type: ignore
    filter = None  # type: ignore

import sys

# 懒解析：避免在 AstrBot 尚未完成初始化时触发导入顺序问题
# （aiocqhttp_message_event 与 astrbot.api / star_tools 之间存在循环导入，
#  在正常的插件加载阶段该模块一定已经加载完毕）。
AiocqhttpMessageEvent = None
_AIOCQHTTP_OK = False
_IMPORT_ERR = None


def _resolve_event_cls():
    """返回 AiocqhttpMessageEvent 类（可用时），否则 None。"""
    global AiocqhttpMessageEvent, _AIOCQHTTP_OK, _IMPORT_ERR
    if AiocqhttpMessageEvent is not None:
        return AiocqhttpMessageEvent

    # 1) 优先从已加载模块中获取（运行时该模块通常已加载）
    module = sys.modules.get(
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
    )
    if module is not None and hasattr(module, "AiocqhttpMessageEvent"):
        AiocqhttpMessageEvent = module.AiocqhttpMessageEvent
        _AIOCQHTTP_OK = True
        return AiocqhttpMessageEvent

    # 2) 兜底：主动导入
    try:
        import astrbot.api  # noqa: F401  先确保 astrbot.api 初始化，规避循环导入

        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent as _cls,
        )

        AiocqhttpMessageEvent = _cls
        _AIOCQHTTP_OK = True
    except Exception as e:  # pragma: no cover
        _IMPORT_ERR = e
    return AiocqhttpMessageEvent

_ORIG_ATTR = "_stream_quote_original_send_streaming"
_PLUGIN_ATTR = "_stream_quote_plugin"

DEFAULT_CONFIG = {
    "enable": True,
    "quote_in_group": True,
    "quote_in_private": True,
    "respect_reply_with_quote": True,
    "debug": False,
}


# --------------------------------------------------------------------------- #
# 配置读取
# --------------------------------------------------------------------------- #
def _current_plugin():
    cls = _resolve_event_cls()
    if cls is None:
        return None
    return getattr(cls, _PLUGIN_ATTR, None)


def _cfg(plugin, key):
    default = DEFAULT_CONFIG.get(key)
    if plugin is None:
        return default
    cfg = getattr(plugin, "config", None)
    if not cfg:
        return default
    try:
        value = cfg.get(key, default)
    except Exception:
        return default
    return default if value is None else value


def _should_quote(plugin, event) -> bool:
    if not _cfg(plugin, "enable"):
        return False

    is_group = bool(event.get_group_id())
    if is_group and not _cfg(plugin, "quote_in_group"):
        return False
    if (not is_group) and not _cfg(plugin, "quote_in_private"):
        return False

    # 可选：跟随主配置里的「回复时引用发送人消息」开关
    if _cfg(plugin, "respect_reply_with_quote"):
        try:
            core_cfg = plugin.context.get_config()
            if not core_cfg.get("platform_settings", {}).get("reply_with_quote", False):
                return False
        except Exception:
            # 读不到主配置时不阻止功能
            pass
    return True


def _inject_reply(event, message) -> bool:
    """往消息链头部插入 Reply，成功返回 True。"""
    chain = getattr(message, "chain", None)
    if not chain:
        return False

    # 已经有引用了就不再插入
    for comp in chain:
        if isinstance(comp, Reply):
            return True

    # 至少要有一个“有内容”的消息段，避免发出一条空引用
    has_content = False
    for comp in chain:
        if isinstance(comp, Plain):
            if comp.text and comp.text.strip():
                has_content = True
                break
        else:
            has_content = True
            break
    if not has_content:
        return False

    message_id = getattr(event.message_obj, "message_id", None)
    if message_id is None:
        return False

    chain.insert(0, Reply(id=str(message_id)))
    return True


# --------------------------------------------------------------------------- #
# 补丁
# --------------------------------------------------------------------------- #
async def _patched_send_streaming(self, generator, use_fallback: bool = False):
    cls = _resolve_event_cls()
    plugin = _current_plugin()
    original = getattr(cls, _ORIG_ATTR)

    if plugin is None or not _should_quote(plugin, self):
        return await original(self, generator, use_fallback)

    state = {"done": False}
    original_send = self.send

    async def _send_with_quote(message):
        if not state["done"]:
            try:
                if _inject_reply(self, message):
                    state["done"] = True
                    if _cfg(plugin, "debug"):
                        logger.info("[stream_quote] 已为流式首条消息注入引用")
            except Exception as e:
                logger.warning(f"[stream_quote] 注入引用失败: {e}")
        return await original_send(message)

    self.send = _send_with_quote
    try:
        return await original(self, generator, use_fallback)
    finally:
        try:
            del self.send
        except AttributeError:
            pass


def _install_patch(plugin) -> None:
    cls = _resolve_event_cls()
    if cls is None:
        logger.error(
            f"[stream_quote] 无法导入 aiocqhttp 适配器，插件不生效: {_IMPORT_ERR}"
        )
        return
    # 只捕获一次真正的原始方法，避免重载插件时叠加包装
    if not hasattr(cls, _ORIG_ATTR):
        setattr(
            cls,
            _ORIG_ATTR,
            cls.send_streaming,
        )
    setattr(cls, _PLUGIN_ATTR, plugin)
    cls.send_streaming = _patched_send_streaming
    logger.info("[stream_quote] 已启用：流式输出首条消息将引用用户消息。")


def _uninstall_patch() -> None:
    cls = _resolve_event_cls()
    if cls is None:
        return
    original = getattr(cls, _ORIG_ATTR, None)
    if original is not None:
        cls.send_streaming = original
    if hasattr(cls, _PLUGIN_ATTR):
        try:
            delattr(cls, _PLUGIN_ATTR)
        except Exception:
            pass
    logger.info("[stream_quote] 已停用。")


# --------------------------------------------------------------------------- #
# 插件主体
# --------------------------------------------------------------------------- #
class StreamQuotePlugin(Star):
    """流式输出时的引用回复插件。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config if config is not None else {}
        try:
            for k, v in DEFAULT_CONFIG.items():
                self.config.setdefault(k, v)
        except Exception:
            pass
        _install_patch(self)

    if filter is not None:

        @filter.command("streamquote", alias={"流式引用"})
        async def streamquote(self, event: "AstrMessageEvent"):
            """查看流式引用插件当前状态。"""
            enabled = _cfg(self, "enable")
            yield event.plain_result(
                "流式引用插件状态：\n"
                f"- 插件启用：{enabled}\n"
                f"- 群聊引用首条：{_cfg(self, 'quote_in_group')}\n"
                f"- 私聊引用首条：{_cfg(self, 'quote_in_private')}\n"
                f"- 跟随主配置 reply_with_quote：{_cfg(self, 'respect_reply_with_quote')}\n"
                "（需同时开启 AstrBot 的流式输出才可见效果）"
            )

    async def terminate(self):
        _uninstall_patch()
