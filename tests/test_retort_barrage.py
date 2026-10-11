"""不依赖 AstrBot 安装的连续回怼插件回归测试。"""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class At:
    def __init__(self, qq="", name=""):
        self.qq = qq
        self.name = name


class MessageChain:
    def __init__(self):
        self.parts = []

    def at(self, name, qq):
        self.parts.append(("at", name, qq))
        return self

    def message(self, text):
        self.parts.append(("text", text))
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


class Event:
    def __init__(self, targets=(), self_id="bot", group_id="group"):
        self.message_obj = types.SimpleNamespace(
            self_id=self_id, message=[At(target) for target in targets]
        )
        self.unified_msg_origin = "umo"
        self._group_id = group_id

    def get_group_id(self):
        return self._group_id

    def get_platform_id(self):
        return "platform"

    def get_sender_id(self):
        return "starter"

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
    sys.modules.update(modules)


install_astrbot_stubs()
MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"
SPEC = importlib.util.spec_from_file_location("retort_barrage_main", MAIN_PATH)
PLUGIN_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLUGIN_MODULE
SPEC.loader.exec_module(PLUGIN_MODULE)
Plugin = PLUGIN_MODULE.RetortBarragePlugin
RetortJob = PLUGIN_MODULE.RetortJob


async def collect(generator):
    return [item async for item in generator]


class RetortBarrageTests(unittest.IsolatedAsyncioTestCase):
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
        return plugin, context

    async def register_loop(self, plugin, scope="scope", target="123"):
        ready = asyncio.Event()

        async def runner():
            await ready.wait()
            await plugin._retort_loop(scope, "umo", target)

        task = asyncio.create_task(runner())
        plugin._jobs[scope] = RetortJob(task, "starter", target, "umo")
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

        async def blocking_loop(*_args):
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
        plugin._jobs[scope] = RetortJob(blocker, "starter", "123", "umo")
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

    def test_build_pool_modes(self):
        plugin, _ = self.make_plugin()
        self.assertEqual(plugin._build_pool(), list(PLUGIN_MODULE.RETORTS))
        plugin, _ = self.make_plugin(aggressive_enabled=True)
        self.assertEqual(
            plugin._build_pool(),
            list(PLUGIN_MODULE.RETORTS) + list(PLUGIN_MODULE.RETORTS_SPICY),
        )
        plugin, _ = self.make_plugin(aggressive_only=True)
        self.assertEqual(plugin._build_pool(), list(PLUGIN_MODULE.RETORTS_SPICY))
        plugin, _ = self.make_plugin(aggressive_enabled=True, aggressive_only=True)
        self.assertEqual(plugin._build_pool(), list(PLUGIN_MODULE.RETORTS_SPICY))

    async def test_terminate_cancels_all_tasks_and_closes_plugin(self):
        plugin, _ = self.make_plugin()
        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
        for index, task in enumerate(tasks):
            plugin._jobs[str(index)] = RetortJob(task, "starter", str(index), "umo")
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
        plugin._jobs[scope] = RetortJob(blocker, "starter", "123", "umo", sent=4)
        result = await collect(plugin.retort_status(event))
        self.assertIn("4/9", result[0])
        self.assertIn("间隔 3 秒", result[0])
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)

    async def test_closed_plugin_rejects_start(self):
        plugin, _ = self.make_plugin()
        plugin._closed = True
        result = await collect(plugin.start_retort(Event(targets=("123",))))
        self.assertIn("正在停止", result[0])
        self.assertEqual(plugin._jobs, {})


if __name__ == "__main__":
    unittest.main()
