"""AstrBot 连续回怼插件。"""

import asyncio
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_retort_barrage"
PLUGIN_DIR = Path(__file__).resolve().parent
CORPUS_PATH = PLUGIN_DIR / "corpus.json"
CUSTOM_PATH = PLUGIN_DIR / "custom_retorts.txt"
STATS_PATH = PLUGIN_DIR / "stats.json"

# 主题标签：智力 / 外貌 / 家人 / 存在 / 言行；「自定义」供 custom_retorts.txt 使用。
THEMES = ("智力", "外貌", "家人", "存在", "言行", "自定义")
DEFAULT_SURRENDER_KEYWORDS = ("我错了", "我服了", "别怼了", "投降", "饶了我")


def _load_corpus() -> tuple:
    """加载内置语料 corpus.json，返回 (普通池, 激进池)，每条为 (主题, 语句)。"""
    normal: list = []
    spicy: list = []
    try:
        data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        normal = [(str(t), str(s)) for t, s in data.get("retorts", []) if s]
        spicy = [(str(t), str(s)) for t, s in data.get("retorts_spicy", []) if s]
    except FileNotFoundError:
        logger.error("[retort_barrage] 缺少 corpus.json，内置语料为空")
    except Exception as exc:
        logger.error(f"[retort_barrage] corpus.json 解析失败: {exc}")
    return normal, spicy


def _load_custom() -> tuple:
    """加载自定义语料 custom_retorts.txt：# 开头为注释；! 开头进激进池；其余进普通池。"""
    normal: list = []
    spicy: list = []
    if not CUSTOM_PATH.exists():
        return normal, spicy
    try:
        for raw in CUSTOM_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("!"):
                text = line[1:].strip()
                if text:
                    spicy.append(("自定义", text))
            else:
                normal.append(("自定义", line))
    except Exception as exc:
        logger.error(f"[retort_barrage] custom_retorts.txt 读取失败: {exc}")
    return normal, spicy


def _load_stats() -> dict:
    """加载战绩统计 stats.json（不存在或损坏时返回零值）。"""
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        return {
            "total_sent": int(data.get("total_sent", 0)),
            "tasks_started": int(data.get("tasks_started", 0)),
            "per_target": {
                str(k): int(v) for k, v in data.get("per_target", {}).items()
            },
        }
    except Exception:
        return {"total_sent": 0, "tasks_started": 0, "per_target": {}}


_BUILTIN_NORMAL, _BUILTIN_SPICY = _load_corpus()
_CUSTOM_NORMAL, _CUSTOM_SPICY = _load_custom()
RETORTS: list = _BUILTIN_NORMAL + _CUSTOM_NORMAL
RETORTS_SPICY: list = _BUILTIN_SPICY + _CUSTOM_SPICY


@dataclass
class RetortJob:
    task: asyncio.Task
    starter_id: str
    target_ids: list
    umo: str
    theme: str = ""
    sent: int = 0


class RetortBarragePlugin(Star):
    """对群聊中被提及的目标执行可停止的连续回怼。"""

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = config or {}
        self.send_interval = self._bounded_float("send_interval", 5.0, 3.0, 300.0)
        self.max_messages = self._bounded_int("max_messages", 20, 1, 60)
        self.avoid_duplicates = bool(self.config.get("avoid_duplicates", True))
        self.mention_target = bool(self.config.get("mention_target", True))
        self.announce_completion = bool(self.config.get("announce_completion", True))
        self.aggressive_enabled = bool(self.config.get("aggressive_enabled", False))
        self.aggressive_only = bool(self.config.get("aggressive_only", False))
        self.target_blacklist = {
            str(x) for x in self.config.get("target_blacklist", []) if str(x)
        }
        self.starter_whitelist = {
            str(x) for x in self.config.get("starter_whitelist", []) if str(x)
        }
        self.group_cooldown = self._bounded_int("group_cooldown", 0, 0, 86400)
        self.daily_start_limit = self._bounded_int("daily_start_limit", 0, 0, 1000)
        self.surrender_enabled = bool(self.config.get("surrender_enabled", True))
        self.surrender_keywords = [
            str(x) for x in self.config.get("surrender_keywords", []) if str(x)
        ] or list(DEFAULT_SURRENDER_KEYWORDS)
        self.blocked_keywords = [
            str(x) for x in self.config.get("blocked_keywords", []) if str(x)
        ]
        self.image_probability = self._bounded_int("image_probability", 0, 0, 100)
        self.image_urls = [
            str(x) for x in self.config.get("image_urls", []) if str(x)
        ]
        self._jobs: dict[str, RetortJob] = {}
        self._jobs_lock = asyncio.Lock()
        self._closed = False
        self._last_end: dict[str, float] = {}
        self._daily_starts: dict[str, int] = {}
        self._break_times: list[float] = []
        self._paused_until = 0.0
        self._stats = _load_stats()

    def _bounded_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _bounded_float(
        self, key: str, default: float, minimum: float, maximum: float
    ) -> float:
        try:
            value = float(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _scope_key(event: AstrMessageEvent) -> str:
        """同一平台的每个群只允许一个任务；私聊使用会话标识。"""
        group_id = str(event.get_group_id() or "")
        return f"{event.get_platform_id()}:{group_id or event.unified_msg_origin}"

    @staticmethod
    def _extract_targets(event: AstrMessageEvent) -> list:
        """提取指令消息中全部有效且非机器人的 At 目标（按出现顺序去重）。"""
        self_id = str(event.message_obj.self_id or "").strip().lower()
        invalid_targets = {"", "0", "all", "@all", self_id}
        targets: list[str] = []
        seen: set[str] = set()
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                target = str(getattr(component, "qq", "") or "").strip()
                if target.lower() not in invalid_targets and target not in seen:
                    seen.add(target)
                    targets.append(target)
        return targets

    @staticmethod
    def _extract_target(event: AstrMessageEvent) -> str:
        """提取第一个有效 At 目标（兼容旧接口）。"""
        targets = RetortBarragePlugin._extract_targets(event)
        return targets[0] if targets else ""

    def _parse_theme(self, event: AstrMessageEvent) -> tuple:
        """解析指令末尾的可选主题参数，返回 (主题, 错误提示)。"""
        tokens: list[str] = []
        for component in event.message_obj.message:
            if isinstance(component, Comp.Plain):
                tokens.extend(str(getattr(component, "text", "") or "").split())
        tokens = [t for t in tokens if "怼他" not in t and not t.startswith("/")]
        if not tokens:
            return "", ""
        candidate = tokens[-1]
        if candidate in THEMES:
            return candidate, ""
        return "", f"未知主题「{candidate}」，可用主题：{'、'.join(THEMES)}"

    def _make_chain(self, target_id: str, text: str) -> MessageChain:
        chain = MessageChain()
        if self.mention_target:
            chain.at(target_id, target_id).message(f" {text}")
        else:
            chain.message(text)
        return chain

    def _make_image_chain(self, target_id: str, url: str) -> MessageChain:
        chain = MessageChain()
        if self.mention_target:
            chain.at(target_id, target_id).image(url)
        else:
            chain.image(url)
        return chain

    async def _send_chain(self, umo: str, chain: MessageChain) -> bool:
        """发送消息链，并将异常或 AstrBot 的 False 结果统一视为失败。"""
        try:
            sent = await self.context.send_message(umo, chain)
        except Exception as exc:
            logger.warning(f"[retort_barrage] 消息发送异常: {exc}")
            return False
        if sent is False:
            logger.warning("[retort_barrage] AstrBot 未找到对应平台，会话消息未发送")
            return False
        return True

    async def _send_text(self, umo: str, text: str) -> bool:
        return await self._send_chain(umo, MessageChain().message(text))

    async def _sleep(self) -> None:
        await asyncio.sleep(self.send_interval)

    def _build_pool(self, theme: str = "") -> list:
        """按配置构建语料池：纯辛辣仅用激进语料；默认仅普通语料；激进开关混入；可按主题筛选并过滤敏感词。"""
        if self.aggressive_only:
            base = RETORTS_SPICY
        else:
            base = RETORTS
            if self.aggressive_enabled:
                base = RETORTS + RETORTS_SPICY
        pool = [text for tag, text in base if (not theme or tag == theme)]
        if self.blocked_keywords:
            pool = [t for t in pool if not any(k in t for k in self.blocked_keywords)]
        return pool

    def _wants_image(self) -> bool:
        return (
            bool(self.image_urls)
            and self.image_probability > 0
            and random.randint(1, 100) <= self.image_probability
        )

    def _record_send(self, target_id: str) -> None:
        self._stats["total_sent"] += 1
        self._stats["per_target"][target_id] = (
            self._stats["per_target"].get(target_id, 0) + 1
        )
        self._save_stats()

    def _record_start(self) -> None:
        self._stats["tasks_started"] += 1
        self._save_stats()

    def _save_stats(self) -> None:
        try:
            STATS_PATH.write_text(
                json.dumps(self._stats, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[retort_barrage] stats.json 写入失败: {exc}")

    def _register_break(self) -> bool:
        """记录一次熔断；10 分钟内达到 3 次则开启 10 分钟保护期，返回是否触发保护。"""
        now = time.time()
        self._break_times = [t for t in self._break_times if now - t < 600]
        self._break_times.append(now)
        if len(self._break_times) >= 3:
            self._paused_until = now + 600
            self._break_times = []
            logger.warning("[retort_barrage] 10 分钟内连续熔断 3 次，暂停新任务 10 分钟")
            return True
        return False

    async def _retort_loop(self, scope: str, umo: str, target_ids, theme: str = ""):
        if isinstance(target_ids, str):
            target_ids = [target_ids]
        sent = 0
        failures = 0
        pool = self._build_pool(theme)
        try:
            if not pool:
                await self._send_text(
                    umo,
                    "当前配置下没有可用语料（主题无匹配或全部被敏感词过滤），任务自动停止。",
                )
                return
            random.shuffle(pool)
            while not self._closed and sent < self.max_messages:
                if self.avoid_duplicates and not pool:
                    pool = self._build_pool(theme)
                    if not pool:
                        break
                    random.shuffle(pool)
                target_id = target_ids[sent % len(target_ids)]
                if self._wants_image():
                    chain = self._make_image_chain(
                        target_id, random.choice(self.image_urls)
                    )
                else:
                    text = pool.pop() if self.avoid_duplicates else random.choice(pool)
                    chain = self._make_chain(target_id, text)
                if await self._send_chain(umo, chain):
                    sent += 1
                    failures = 0
                    self._record_send(target_id)
                    async with self._jobs_lock:
                        job = self._jobs.get(scope)
                        if job and job.task is asyncio.current_task():
                            job.sent = sent
                else:
                    failures += 1
                    logger.warning(
                        f"[retort_barrage] 连续回怼发送失败 ({failures}/3)"
                    )
                    if failures >= 3:
                        paused = self._register_break()
                        if not self._closed:
                            message = (
                                "连续发送失败 3 次，任务已自动停止（可能触发平台风控）。"
                            )
                            if paused:
                                message += "已进入 10 分钟保护期，期间暂停新的回怼任务。"
                            await self._send_text(umo, message)
                        break

                if sent < self.max_messages and not self._closed:
                    await self._sleep()

            if (
                not self._closed
                and sent >= self.max_messages
                and self.announce_completion
            ):
                await self._send_text(
                    umo, f"已达到本次 {self.max_messages} 条上限，自动停止。"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"[retort_barrage] 后台任务异常: {exc}")
        finally:
            self._last_end[scope] = time.time()
            current = asyncio.current_task()
            async with self._jobs_lock:
                job = self._jobs.get(scope)
                if job and job.task is current:
                    self._jobs.pop(scope, None)

    async def _stop_scope(self, scope: str) -> bool:
        """停止指定群的任务并记录冷却起点，返回是否存在运行中的任务。"""
        async with self._jobs_lock:
            job = self._jobs.pop(scope, None)
        if not job or job.task.done():
            return False
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        self._last_end[scope] = time.time()
        return True

    @filter.command("怼他")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def start_retort(self, event: AstrMessageEvent):
        """开始连续回怼被提及的群成员，支持多目标轮流与主题筛选。"""
        target_ids = self._extract_targets(event)
        if not target_ids:
            yield event.plain_result(
                "请使用：/怼他 @某人 [主题]，可选主题：智力、外貌、家人、存在、言行、自定义"
            )
            return
        theme, theme_error = self._parse_theme(event)
        if theme_error:
            yield event.plain_result(theme_error)
            return
        sender_id = str(event.get_sender_id())
        if self.starter_whitelist and sender_id not in self.starter_whitelist:
            yield event.plain_result("你没有发起回怼的权限。")
            return
        blocked = [t for t in target_ids if t in self.target_blacklist]
        if blocked:
            yield event.plain_result(
                f"目标 {'、'.join(blocked)} 在保护名单中，无法发起回怼。"
            )
            return
        now = time.time()
        if now < self._paused_until:
            wait_minutes = int((self._paused_until - now) // 60) + 1
            yield event.plain_result(
                f"已触发风控保护，约 {wait_minutes} 分钟内暂停发起回怼。"
            )
            return
        scope = self._scope_key(event)
        if self.group_cooldown > 0:
            last_end = self._last_end.get(scope, 0.0)
            if now - last_end < self.group_cooldown:
                wait_seconds = int(self.group_cooldown - (now - last_end)) + 1
                yield event.plain_result(
                    f"本群回怼冷却中，还需等待约 {wait_seconds} 秒。"
                )
                return
        daily_key = f"{time.strftime('%Y-%m-%d')}:{sender_id}"
        if (
            self.daily_start_limit > 0
            and self._daily_starts.get(daily_key, 0) >= self.daily_start_limit
        ):
            yield event.plain_result(
                f"你今天已发起 {self.daily_start_limit} 次回怼，明天再来吧。"
            )
            return
        if not self._build_pool(theme):
            yield event.plain_result(
                "当前配置下没有可用语料（主题无匹配或全部被敏感词过滤）。"
            )
            return

        already_running = False
        plugin_closed = False
        async with self._jobs_lock:
            if self._closed:
                plugin_closed = True
            else:
                existing = self._jobs.get(scope)
                if existing and not existing.task.done():
                    already_running = True
                else:
                    task = asyncio.create_task(
                        self._retort_loop(
                            scope, event.unified_msg_origin, target_ids, theme
                        ),
                        name=f"retort_barrage_{scope}",
                    )
                    self._jobs[scope] = RetortJob(
                        task=task,
                        starter_id=sender_id,
                        target_ids=target_ids,
                        umo=event.unified_msg_origin,
                        theme=theme,
                    )

        if plugin_closed:
            yield event.plain_result("插件正在停止，请稍后重载后再试。")
            return
        if already_running:
            yield event.plain_result("当前群已有回怼任务，请先发送 /停止怼。")
            return
        self._daily_starts[daily_key] = self._daily_starts.get(daily_key, 0) + 1
        self._record_start()

    @filter.command("停止怼")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def stop_retort(self, event: AstrMessageEvent):
        """停止当前群的连续回怼任务。"""
        stopped = await self._stop_scope(self._scope_key(event))
        if stopped:
            yield event.plain_result("已停止回怼。")
        else:
            yield event.plain_result("当前没有正在运行的回怼任务。")

    @filter.command("怼状态")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def retort_status(self, event: AstrMessageEvent):
        """查看当前群的回怼任务状态。"""
        scope = self._scope_key(event)
        async with self._jobs_lock:
            job = self._jobs.get(scope)
        if not job or job.task.done():
            yield event.plain_result("当前没有正在运行的回怼任务。")
            return
        targets = "、".join(job.target_ids)
        theme = job.theme or "全部"
        yield event.plain_result(
            f"当前正在回怼目标 {targets}；主题「{theme}」；已发送 {job.sent}/{self.max_messages} 条，间隔 {self.send_interval:g} 秒。"
        )

    @filter.command("怼统计")
    async def retort_stats(self, event: AstrMessageEvent):
        """查看累计战绩与被怼榜。"""
        stats = self._stats
        lines = [
            f"累计发起任务 {stats['tasks_started']} 次，发送语料 {stats['total_sent']} 条。"
        ]
        top = sorted(
            stats["per_target"].items(), key=lambda kv: kv[1], reverse=True
        )[:5]
        if top:
            ranking = "，".join(f"{qq}（{count} 条）" for qq, count in top)
            lines.append(f"被怼榜 TOP5：{ranking}")
        yield event.plain_result("\n".join(lines))

    @filter.command("怼帮助")
    async def retort_help(self, event: AstrMessageEvent):
        """查看使用说明与免责声明摘要。"""
        if self.aggressive_only:
            mode = "纯辛辣（仅激进语料）"
        elif self.aggressive_enabled:
            mode = "混合（普通+激进语料）"
        else:
            mode = "普通（仅普通语料）"
        lines = [
            "【连续回怼】使用说明",
            "/怼他 @某人 [主题] —— 开始回怼，可 @多人轮流怼",
            f"可选主题：{'、'.join(THEMES)}（默认全部随机）",
            "/停止怼 —— 停止本群任务（任何人可用）",
            "/怼状态 —— 查看当前任务进度",
            "/怼统计 —— 查看战绩统计",
            f"当前模式：{mode}；单次上限 {self.max_messages} 条，间隔 {self.send_interval:g} 秒",
            "被怼的人发送「我错了」等求饶词可自动停止。",
            "本插件仅供娱乐，请勿用于真实攻击；使用风险自担，详见仓库 README 免责声明。",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def watch_surrender(self, event: AstrMessageEvent):
        """监听被怼目标的求饶消息，命中关键词后自动停止本群任务。"""
        if not self.surrender_enabled or self._closed:
            return
        scope = self._scope_key(event)
        async with self._jobs_lock:
            job = self._jobs.get(scope)
            if not job or job.task.done():
                return
            is_target = str(event.get_sender_id()) in job.target_ids
        if not is_target:
            return
        text = str(event.message_str or "")
        if not any(keyword in text for keyword in self.surrender_keywords):
            return
        await self._stop_scope(scope)
        yield event.plain_result("对方已求饶，本群回怼任务自动停止。得饶人处且饶人。")

    async def terminate(self):
        """插件停用或重载时清理全部后台任务。"""
        self._closed = True
        async with self._jobs_lock:
            tasks = [job.task for job in self._jobs.values()]
            self._jobs.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[retort_barrage] 插件已停止，后台任务已清理")