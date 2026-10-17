"""不依赖 AstrBot 安装的连续回怼插件回归测试。"""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class At:
    def __init__(self, qq="", name=""):
        self.qq = qq
        self.name = name


class Plain:
    def __init__(self, text=""):
        self.text = text


class MessageChain:
    def __init__(self):
        self.parts = []

    def at(self, name, qq):
        self.parts.append(("at", name, qq))
        return self

    def message(self, text):
        self.parts.append(("text", text))
        return self

    def image(self, url):
        self.parts.append(("image", url))
        return self


class DummyFilter:
    class EventMessageType:
        GROUP_MESSAGE = "group"

    @staticmethod
    def command(_name):
        return lambda func: func

    @staticmethod
    def event_message_type(_message_type):
        return lambda func: func


class Star:
    def __init__(self, context):
        self.context = context


class DummyLogger:
    warning = staticmethod(lambda *_args, **_kwargs: None)
    exception = staticmethod(lambda *_args, **_kwargs: None)
    info = staticmethod(lambda *_args, **_kwargs: None)
    error = staticmethod(lambda *_args, **_kwargs: None)


class Event:
    def __init__(self, targets=(), self_id="bot", group_id="group", text="", sender="starter"):
        self.message_obj = types.SimpleNamespace(
            self_id=self_id,
            message=[Plain(text)] + [At(target) for target in targets],
        )
        self.message_str = text
        self.unified_msg_origin = "umo"
        self._group_id = group_id
        self._sender = sender

    def get_group_id(self):
        return self._group_id

    def get_platform_id(self):
        return "platform"

    def get_sender_id(self):
        return self._sender

    def plain_result(self, text):
        return text


def install_astrbot_stubs():
    modules = {
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": types.ModuleType("astrbot.api"),
        "astrbot.api.event": types.ModuleType("astrbot.api.event"),
        "astrbot.api.star": types.ModuleType("astrbot.api.star"),
        "astrbot.api.message_components": types.ModuleType(
            "astrbot.api.message_components"
        ),
    }
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = DummyLogger()
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].MessageChain = MessageChain
    modules["astrbot.api.event"].filter = DummyFilter
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.message_components"].At = At
    modules["astrbot.api.message_components"].Plain = Plain
    sys.modules.update(modules)


install_astrbot_stubs()
MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"
SPEC = importlib.util.spec_from_file_location("retort_barrage_main", MAIN_PATH)
PLUGIN_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLUGIN_MODULE
SPEC.loader.exec_module(PLUGIN_MODULE)
Plugin = PLUGIN_MODULE.RetortBarragePlugin
RetortJob = PLUGIN_MODULE.RetortJob

EMPTY_POOL_MESSAGE = "没有可用语料"


async def collect(generator):
    return [item async for item in generator]


class RetortBarrageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 每个用例使用独立的临时文件，避免运行时模式/统计互相污染
        self._runtime_file = Path(tempfile.gettempdir()) / (
            f"retort_runtime_{os.getpid()}_{id(self)}.json"
        )
        self._stats_file = Path(tempfile.gettempdir()) / (
            f"retort_stats_{os.getpid()}_{id(self)}.json"
        )
        patch.object(PLUGIN_MODULE, "RUNTIME_PATH", self._runtime_file).start()
        patch.object(PLUGIN_MODULE, "STATS_PATH", self._stats_file).start()
        self.addCleanup(patch.stopall)

    def make_plugin(self, side_effect=True, **config):
        if isinstance(side_effect, BaseException):
            send_message = AsyncMock(side_effect=side_effect)
        else:
            send_message = AsyncMock(return_value=side_effect)
        context = types.SimpleNamespace(send_message=send_message)
        defaults = {
            "send_interval": 3,
            "max_messages": 2,
            "announce_completion": False,
        }
        defaults.update(config)
        plugin = Plugin(context, defaults)
        plugin._stats = {"total_sent": 0, "tasks_started": 0, "per_target": {}}
        return plugin, context

    async def register_loop(self, plugin, scope="scope", target="123"):
        ready = asyncio.Event()

        async def runner():
            await ready.wait()
            await plugin._retort_loop(scope, "umo", [target])

        task = asyncio.create_task(runner())
        plugin._jobs[scope] = RetortJob(task, "starter", [target], "umo")
        ready.set()
        return task

    async def test_success_reaches_limit_updates_count_and_cleans(self):
        plugin, context = self.make_plugin()
        with patch.object(plugin, "_sleep", AsyncMock()) as sleep:
            task = await self.register_loop(plugin)
            job = plugin._jobs["scope"]
            await task
        self.assertEqual(job.sent, 2)
        self.assertEqual(context.send_message.await_count, 2)
        self.assertNotIn("scope", plugin._jobs)
        self.assertEqual(sleep.await_count, 1)

    async def test_false_three_times_stops_without_success_count(self):
        plugin, context = self.make_plugin(side_effect=False)
        with patch.object(plugin, "_sleep", AsyncMock()):
            task = await self.register_loop(plugin)
            job = plugin._jobs["scope"]
            await task
        self.assertEqual(job.sent, 0)
        self.assertEqual(context.send_message.await_count, 4)
        self.assertNotIn("scope", plugin._jobs)

    async def test_exception_three_times_stops(self):
        plugin, context = self.make_plugin(side_effect=RuntimeError("boom"))
        with patch.object(plugin, "_sleep", AsyncMock()):
            task = await self.register_loop(plugin)
            job = plugin._jobs["scope"]
            await task
        self.assertEqual(job.sent, 0)
        self.assertEqual(context.send_message.await_count, 4)
        self.assertNotIn("scope", plugin._jobs)

    async def test_cancel_cleans_registered_job(self):
        plugin, _ = self.make_plugin()
        entered_sleep = asyncio.Event()

        async def blocking_sleep():
            entered_sleep.set()
            await asyncio.Event().wait()

        with patch.object(plugin, "_sleep", blocking_sleep):
            task = await self.register_loop(plugin)
            await entered_sleep.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertNotIn("scope", plugin._jobs)

    async def test_successful_start_has_no_confirmation_reply(self):
        plugin, _ = self.make_plugin()

        async def blocking_loop(*_args, **_kwargs):
            await asyncio.Event().wait()

        with patch.object(plugin, "_retort_loop", blocking_loop):
            event = Event(targets=("123",))
            result = await collect(plugin.start_retort(event))
            self.assertEqual(result, [])
            self.assertEqual(len(plugin._jobs), 1)
            await plugin.terminate()

    async def test_duplicate_start_does_not_create_second_task(self):
        plugin, _ = self.make_plugin()
        blocker = asyncio.create_task(asyncio.Event().wait())
        scope = plugin._scope_key(Event(targets=("123",)))
        plugin._jobs[scope] = RetortJob(blocker, "starter", ["123"], "umo")
        result = await collect(plugin.start_retort(Event(targets=("456",))))
        self.assertEqual(len(plugin._jobs), 1)
        self.assertIn("已有回怼任务", result[0])
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)

    def test_extract_target_rejects_bot_all_zero_and_missing(self):
        self.assertEqual(Plugin._extract_target(Event(targets=("bot",))), "")
        self.assertEqual(Plugin._extract_target(Event(targets=("all",))), "")
        self.assertEqual(Plugin._extract_target(Event(targets=("@ALL",))), "")
        self.assertEqual(Plugin._extract_target(Event(targets=("0",))), "")
        self.assertEqual(Plugin._extract_target(Event()), "")
        self.assertEqual(
            Plugin._extract_target(Event(targets=("all", "123"))), "123"
        )
        self.assertEqual(
            Plugin._extract_targets(Event(targets=("123", "123", "456"))),
            ["123", "456"],
        )

    def test_build_pool_modes(self):
        plugin, _ = self.make_plugin()
        self.assertEqual(
            plugin._build_pool(), [t for _, t in PLUGIN_MODULE.RETORTS]
        )
        plugin, _ = self.make_plugin(aggressive_enabled=True)
        self.assertEqual(
            plugin._build_pool(),
            [t for _, t in PLUGIN_MODULE.RETORTS]
            + [t for _, t in PLUGIN_MODULE.RETORTS_SPICY],
        )
        plugin, _ = self.make_plugin(aggressive_only=True)
        self.assertEqual(
            plugin._build_pool(), [t for _, t in PLUGIN_MODULE.RETORTS_SPICY]
        )
        plugin, _ = self.make_plugin(aggressive_enabled=True, aggressive_only=True)
        self.assertEqual(
            plugin._build_pool(), [t for _, t in PLUGIN_MODULE.RETORTS_SPICY]
        )

    def test_build_pool_theme_and_keyword_filter(self):
        plugin, _ = self.make_plugin(aggressive_only=True)
        family = [t for tag, t in PLUGIN_MODULE.RETORTS_SPICY if tag == "家人"]
        self.assertEqual(plugin._build_pool("家人"), family)
        plugin, _ = self.make_plugin()
        self.assertEqual(plugin._build_pool("家人"), [])
        plugin, _ = self.make_plugin(blocked_keywords=["脑子"])
        pool = plugin._build_pool()
        self.assertNotEqual(pool, [t for _, t in PLUGIN_MODULE.RETORTS])
        self.assertTrue(all("脑子" not in t for t in pool))

    async def test_terminate_cancels_all_tasks_and_closes_plugin(self):
        plugin, _ = self.make_plugin()
        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
        for index, task in enumerate(tasks):
            plugin._jobs[str(index)] = RetortJob(task, "starter", [str(index)], "umo")
        await plugin.terminate()
        self.assertTrue(plugin._closed)
        self.assertEqual(plugin._jobs, {})
        self.assertTrue(all(task.cancelled() for task in tasks))

    async def test_no_sleep_after_last_success(self):
        plugin, _ = self.make_plugin(max_messages=1)
        with patch.object(plugin, "_sleep", AsyncMock()) as sleep:
            task = await self.register_loop(plugin)
            await task
        sleep.assert_not_awaited()

    async def test_status_reports_sent_limit_and_interval(self):
        plugin, _ = self.make_plugin(max_messages=9)
        blocker = asyncio.create_task(asyncio.Event().wait())
        event = Event(targets=("123",))
        scope = plugin._scope_key(event)
        plugin._jobs[scope] = RetortJob(blocker, "starter", ["123"], "umo", sent=4)
        result = await collect(plugin.retort_status(event))
        self.assertIn("4/9", result[0])
        self.assertIn("123", result[0])
        self.assertIn("间隔 3 秒", result[0])
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)

    async def test_closed_plugin_rejects_start(self):
        plugin, _ = self.make_plugin()
        plugin._closed = True
        result = await collect(plugin.start_retort(Event(targets=("123",))))
        self.assertIn("正在停止", result[0])
        self.assertEqual(plugin._jobs, {})

    def test_parse_theme_valid_and_invalid(self):
        plugin, _ = self.make_plugin()
        self.assertEqual(
            plugin._parse_theme(Event(targets=("123",), text="怼他 智力")), ("智力", "")
        )
        self.assertEqual(plugin._parse_theme(Event(targets=("123",), text="怼他")), ("", ""))
        theme, error = plugin._parse_theme(Event(targets=("123",), text="怼he 无效主题"))
        self.assertEqual(theme, "")
        self.assertIn("未知主题", error)

    async def test_start_rejects_unknown_theme(self):
        plugin, _ = self.make_plugin()
        result = await collect(plugin.start_retort(Event(targets=("123",), text="怼他 无效主题")))
        self.assertIn("未知主题", result[0])

    async def test_start_rejects_blacklist_and_whitelist(self):
        plugin, _ = self.make_plugin(target_blacklist=["123"])
        result = await collect(plugin.start_retort(Event(targets=("123",))))
        self.assertIn("保护名单", result[0])
        plugin, _ = self.make_plugin(starter_whitelist=["999"])
        result = await collect(plugin.start_retort(Event(targets=("123",))))
        self.assertIn("权限", result[0])

    async def test_cooldown_and_daily_limit(self):
        plugin, _ = self.make_plugin(group_cooldown=300)
        scope = plugin._scope_key(Event(targets=("123",)))
        plugin._last_end[scope] = time.time()
        result = await collect(plugin.start_retort(Event(targets=("123",))))
        self.assertIn("冷却", result[0])

        plugin, _ = self.make_plugin(daily_start_limit=1)

        async def blocking_loop(*_args, **_kwargs):
            await asyncio.Event().wait()

        with patch.object(plugin, "_retort_loop", blocking_loop):
            result = await collect(plugin.start_retort(Event(targets=("123",))))
            self.assertEqual(result, [])
            result2 = await collect(plugin.start_retort(Event(targets=("456",))))
            self.assertIn("明天再来", result2[0])
            await plugin.terminate()

    async def test_surrender_stops_task(self):
        plugin, _ = self.make_plugin()

        async def blocking_loop(*_args, **_kwargs):
            await asyncio.Event().wait()

        with patch.object(plugin, "_retort_loop", blocking_loop):
            await collect(plugin.start_retort(Event(targets=("123",))))
            scope = plugin._scope_key(Event(targets=("123",)))
            job = plugin._jobs[scope]

            result = await collect(plugin.watch_surrender(Event(text="我错了", sender="123")))
            self.assertTrue(any("求饶" in r for r in result))
            await asyncio.gather(job.task, return_exceptions=True)
            self.assertTrue(job.task.cancelled())
            self.assertNotIn(scope, plugin._jobs)

            await collect(plugin.start_retort(Event(targets=("123",))))
            job2 = plugin._jobs[scope]
            result2 = await collect(plugin.watch_surrender(Event(text="我错了", sender="999")))
            self.assertEqual(result2, [])
            self.assertIn(scope, plugin._jobs)
            await plugin.terminate()

    async def test_surrender_disabled_ignores_message(self):
        plugin, _ = self.make_plugin(surrender_enabled=False)

        async def blocking_loop(*_args, **_kwargs):
            await asyncio.Event().wait()

        with patch.object(plugin, "_retort_loop", blocking_loop):
            await collect(plugin.start_retort(Event(targets=("123",))))
            scope = plugin._scope_key(Event(targets=("123",)))
            result = await collect(plugin.watch_surrender(Event(text="我错了", sender="123")))
            self.assertEqual(result, [])
            self.assertIn(scope, plugin._jobs)
            await plugin.terminate()

    async def test_multi_target_rotation(self):
        plugin, context = self.make_plugin(max_messages=2)
        ready = asyncio.Event()

        async def runner():
            await ready.wait()
            await plugin._retort_loop("scope", "umo", ["1", "2"])

        task = asyncio.create_task(runner())
        plugin._jobs["scope"] = RetortJob(task, "starter", ["1", "2"], "umo")
        with patch.object(plugin, "_sleep", AsyncMock()):
            ready.set()
            await task
        chains = [call.args[1] for call in context.send_message.await_args_list]
        self.assertEqual([chain.parts[0][2] for chain in chains], ["1", "2"])

    async def test_empty_pool_guard(self):
        plugin, context = self.make_plugin()
        with patch.object(plugin, "_build_pool", lambda theme="": []):
            task = await self.register_loop(plugin)
            await task
        self.assertEqual(context.send_message.await_count, 1)
        text = context.send_message.await_args_list[0].args[1].parts[0][1]
        self.assertIn(EMPTY_POOL_MESSAGE, text)
        self.assertNotIn("scope", plugin._jobs)

    async def test_image_chain(self):
        plugin, context = self.make_plugin(
            max_messages=1,
            image_urls=["http://example.com/meme.jpg"],
            image_probability=100,
        )
        task = await self.register_loop(plugin)
        await task
        chain = context.send_message.await_args_list[0].args[1]
        self.assertIn(("image", "http://example.com/meme.jpg"), chain.parts)
        self.assertIn(("at", "123", "123"), chain.parts)

    async def test_stats_recorded(self):
        plugin, _ = self.make_plugin()
        with patch.object(plugin, "_sleep", AsyncMock()):
            task = await self.register_loop(plugin)
            await task
        self.assertEqual(plugin._stats["total_sent"], 2)
        self.assertEqual(plugin._stats["per_target"].get("123"), 2)

    async def test_mode_switch_commands(self):
        plugin, _ = self.make_plugin()
        result = await collect(plugin.enable_aggressive(Event()))
        self.assertTrue(plugin.aggressive_enabled)
        self.assertFalse(plugin.aggressive_only)
        self.assertIn("混合", result[0])
        result = await collect(plugin.enable_pure_spicy(Event()))
        self.assertTrue(plugin.aggressive_only)
        self.assertIn("纯辛辣", result[0])
        result = await collect(plugin.disable_pure_spicy(Event()))
        self.assertFalse(plugin.aggressive_only)
        self.assertTrue(plugin.aggressive_enabled)
        result = await collect(plugin.disable_aggressive(Event()))
        self.assertFalse(plugin.aggressive_enabled)
        self.assertFalse(plugin.aggressive_only)
        result = await collect(plugin.retort_mode(Event()))
        self.assertIn("普通", result[0])
        self.assertTrue(self._runtime_file.exists())
        self.assertEqual(
            json.loads(self._runtime_file.read_text(encoding="utf-8")),
            {"aggressive_enabled": False, "aggressive_only": False},
        )

    async def test_mode_switch_denied(self):
        plugin, _ = self.make_plugin(starter_whitelist=["999"])
        result = await collect(plugin.enable_aggressive(Event(sender="123")))
        self.assertIn("权限", result[0])
        self.assertFalse(plugin.aggressive_enabled)
        result = await collect(plugin.enable_pure_spicy(Event(sender="123")))
        self.assertIn("权限", result[0])
        self.assertFalse(plugin.aggressive_only)

    def test_runtime_mode_loaded_on_init(self):
        self._runtime_file.write_text(
            json.dumps({"aggressive_enabled": True, "aggressive_only": True}),
            encoding="utf-8",
        )
        plugin, _ = self.make_plugin()
        self.assertTrue(plugin.aggressive_enabled)
        self.assertTrue(plugin.aggressive_only)


if __name__ == "__main__":
    unittest.main()