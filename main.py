"""AstrBot 连续回怼插件。"""

import asyncio
import random
from dataclasses import dataclass
from typing import Optional

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_retort_barrage"

# 第二人称辛辣语料：攻击发言、逻辑和行为，不包含威胁、歧视、隐私或家人攻击。
RETORTS = (
    "你这张嘴要是能拿来干正事，也不至于一开口就只剩丢人现眼。",
    "你脑子里的水要是倒出来，估计都够拖地了。",
    "你别急着开口，没人把你当哑巴，但你一说话大家真容易把你当傻子。",
    "你这理解能力，不能说一塌糊涂，只能说糊涂得相当稳定。",
    "你装得挺像那么回事，可惜一开口就露馅。",
    "你说话跟放屁最大的区别，大概是放屁偶尔还能让人警醒。",
    "你但凡有一丁点逻辑，也不至于把屁话说得这么理直气壮。",
    "你这脑回路不是清奇，是压根没通电。",
    "你可真牛逼，硬是把无知说出了新闻发布会的气势。",
    "你那点可怜的认知，撑不起这么大的嗓门。",
    "你别光顾着嘴硬，先看看自己的道理是不是已经稀碎了。",
    "你这种水平还敢出来抬杠，主打的就是一个不要脸也不怕丢人。",
    "你不是观点独特，你只是单纯地没弄明白。",
    "你每次开口，都像在给愚蠢做免费宣传。",
    "你挺适合保持沉默的，至少那样别人还能对你的智商保留一点幻想。",
    "你这不是直率，是没教养还非要给自己贴金。",
    "你那副自以为是的样子，和你空空如也的逻辑配得正好。",
    "你先别忙着反驳，毕竟组织一句像样的话对你来说挺费劲。",
    "你把不要脸发挥得这么稳定，也算有一项长期坚持的特长。",
    "你嘴上跑的不是火车，是一整套报废线路。",
    "你这点破见识还到处显摆，跟端着垃圾桶请人鉴赏没区别。",
    "你说了半天，除了证明自己又蠢又犟，什么也没证明。",
    "你不是能言善辩，你只是废话多到别人懒得打断。",
    "你这人最大的本事，就是把简单的丢脸搞得声势浩大。",
    "你脑子要是有嘴一半勤快，也不至于蠢得这么醒目。",
    "你别摆出一副懂王的样子，你那点东西经不起半句追问。",
    "你说话前要是肯过一下脑子，也不至于句句都像事故现场。",
    "你是怎么做到每个字都认识，凑一起却全是屁话的？",
    "你不是没救，你只是连自己哪里有毛病都看不明白。",
    "你少拿无知当个性，没人有义务欣赏你这坨破逻辑。",
    "你这一开口，空气都嫌自己被浪费了。",
    "你可闭嘴吧，再说下去连你仅剩的那点体面都保不住。",
    "你那不是自信，是脑子没货还拒绝盘点。",
    "你以为自己在输出观点，其实只是在公开处理智商库存。",
    "你这杠抬得毫无技术含量，纯靠脸皮厚硬撑。",
    "你连事实都没弄清楚，就敢把屁放得震天响。",
    "你那几句车轱辘废话，翻来覆去也洗不出半点道理。",
    "你可以继续嘴硬，反正丢脸的又不是别人。",
    "你这副德行最神奇的地方，是蠢得明明白白还自我感觉良好。",
    "你不是让人说服不了，你是根本听不懂正常逻辑。",
    "你最好珍惜别人还愿意搭理你的时候，毕竟笑话看久了也会腻。",
    "你这水平就别强行发表意见了，沉默至少不会暴露得这么彻底。",
    "你说话这么冲，不知道的还以为你有道理，仔细一听原来只有脾气。",
    "你把胡说八道包装成直言不讳，脸皮确实比逻辑结实。",
    "你连最基本的道理都接不住，就别装出一副高深莫测的蠢样。",
    "你这人挺省事，别人还没骂你，你自己一开口就把缺点全交代了。",
    "你要是真有本事，就不会只剩一张破嘴到处找存在感。",
    "你这不是在交流，是在拿大家的耐心给你的愚蠢垫背。",
    "你一张嘴就把没脑子三个字演得活灵活现。",
    "你这点智商还四处乱晃，也不怕半路掉光。",
    "你说的每句话都在提醒别人，投胎成人不等于学会思考。",
    "你真是屎壳郎戴面具，臭不要脸还硬装体面。",
    "你那脑袋纯粹就是个摆设，除了显高一点屁用没有。",
    "你说话像厕所堵了三天，除了恶心就是添堵。",
    "你这副欠收拾的嘴脸，和你那堆狗屁道理简直绝配。",
    "你别把嗓门当脑子，叫得再响也掩盖不了你满嘴放屁。",
    "你这人不光脑子空，嘴还漏风，什么垃圾都往外倒。",
    "你以为自己是个角色，其实顶多算个碍眼的路障。",
    "你这智商拿去回收，废品站都嫌分类困难。",
    "你一说话就像垃圾桶翻了，内容没有，味道挺冲。",
    "你这套狗屁理论，连拿来擦鞋都嫌硌脚。",
    "你脸皮厚得能防弹，可惜脑子薄得挡不住一句实话。",
    "你满嘴喷粪还觉得自己口才好，真是对语言最大的侮辱。",
    "你别再瞎逼逼了，废话也得有个质量底线。",
    "你这蠢劲儿不是偶尔发挥，是全天候稳定供应。",
    "你脑子估计欠费停机了，只剩嘴还在违规营业。",
    "你那点本事全长脸皮上了，难怪说话一点分量都没有。",
    "你说得这么认真，我差点忘了这全是一堆狗屁。",
    "你这张嘴最大的贡献，就是让沉默显得格外珍贵。",
    "你不用故意装傻，你平时的表现已经非常自然。",
    "你每说一句话，都在给自己的愚蠢补充证据。",
    "你别跟我谈格局，你连一句人话都组织不明白。",
    "你这种脑子配上这张嘴，属于垃圾分类里的混合废物。",
    "你活像一本印错的说明书，内容扯淡还害人理解。",
    "你说话这么没谱，指南针见了你都得原地辞职。",
    "你这破逻辑东拼西凑，乞丐看了都嫌寒碜。",
    "你少在这儿装蒜，你顶多算一瓣发霉的。",
    "你脑容量不大，装的狗屁倒是一点不少。",
    "你这人一开口，文化两个字都想连夜逃跑。",
    "你不是嘴毒，你是脑子坏了以后废话往外泄。",
    "你连胡说八道都说不出新意，废物得相当全面。",
    "你长了一张能说话的嘴，可惜没配一个会思考的脑子。",
    "你要是把吹牛的劲儿用来长脑子，现在也不至于这样。",
    "你这副蠢样不用化妆，往那一站就是完整节目。",
    "你说话像往清水里倒泔水，没营养还毁气氛。",
    "你那点道理比纸还薄，脸皮倒比城墙还厚。",
    "你别再给自己加戏了，没人愿意看傻子独角戏。",
    "你这智商能活到现在，运气确实比能力强多了。",
    "你嘴里的废话要是能发电，整座城都不用交电费。",
    "你那副拽样挺唬人，一听内容才发现全是空壳。",
    "你这人最大的优点，大概是缺点暴露得特别主动。",
    "你说一句蠢话不稀奇，句句都蠢才是你的硬实力。",
    "你那点破脾气别到处显摆，没人惯着你这臭毛病。",
    "你把没素质当真性情，把没脑子当有个性，真够可笑。",
    "你说话之前能不能先漱漱脑子，味儿实在太冲。",
    "你这一肚子坏水没什么杀伤力，主要是看着恶心。",
    "你也别怪别人瞧不起你，毕竟你自己一直在提供素材。",
    "你这认知水平，跟井底之蛙比都算视野狭窄。",
    "你说话全凭一股蛮劲，像拿马桶刷指点江山。",
    "你真把自己当盘菜了，可惜端上桌也只配倒掉。",
    "你这点出息，除了窝里横就是网上瞎叫唤。",
    "你那脑回路弯得离谱，狗看了都找不到回家的路。",
    "你别用那点半吊子见识挑战常识，挺丢人的。",
    "你说的话狗屁不通，偏偏表情还像刚发现真理。",
    "你不是单纯地蠢，你还特别努力地让所有人知道。",
    "你这张嘴像漏水的垃圾袋，走到哪儿脏到哪儿。",
    "你装腔作势的样子挺专业，可惜内容业余得可怜。",
    "你讲道理讲不过就撒泼，确实把无能演得很完整。",
    "你这点可怜的优越感，到底是谁施舍给你的？",
    "你别在那里狗叫了，有理说理，没理就滚去补课。",
    "你说话像把垃圾嚼碎了再吐出来，费劲又恶心。",
    "你不是反应慢，你是脑子根本没参与这场对话。",
    "你那点小心思写满脸上了，还装什么高明。",
    "你这破嘴一刻不停，脑子却像放了永久长假。",
    "你连自己说过什么都圆不回来，还好意思硬撑。",
    "你那堆废话唯一清楚的地方，就是清楚地暴露了无知。",
    "你这辈子要是少说几句屁话，形象都能抢救回来一点。",
    "你把自己看得太高，把别人耐心看得太不值钱。",
    "你不是胆子大，你只是蠢到意识不到自己多可笑。",
    "你这脑袋里要真有东西，也不会只会重复那几句臭屁。",
    "你可别继续装明白人了，演技差得让人替你尴尬。",
    "你这话说出口，连狗屁两个字都觉得受了侮辱。",
    "你除了胡搅蛮缠，还会不会一点像人的沟通方式？",
    "你这么爱显摆无知，是怕别人发现得不够快吗？",
    "你那张破嘴要是有刹车，也不至于一路丢人到现在。",
    "你别急着证明自己没救，大家早就看明白了。",
    "你说话像拿臭袜子捂人脸，没道理，只剩下膈应。",
    "你这智力水平不适合抬杠，比较适合安静旁听。",
    "你满脑子浆糊还摇得挺响，生怕别人不知道里面是空的。",
    "你这种人最擅长的，就是把别人的耐心当成自己犯贱的资本。",
    "你话说得越多，越显得沉默对你是一种奢侈的体面。",
    "你先把自己那堆烂逻辑收拾干净，再出来指手画脚。",
    "你这张脸要配得上你的自信，至少得再厚三层。",
    "你不是不可理喻，你是压根不配让道理浪费时间。",
    "你浑身上下最硬的就是嘴，最软的就是那点可怜的逻辑。",
    "你继续放你的狗屁，反正正常人已经把你当背景噪音了。",
    "你这套说辞看着挺完整，可惜从开头到结尾都没沾上逻辑。",
    "你别把固执当坚持，错得再坚定也不会自动变成正确。",
    "你一边逃避事实一边高谈阔论，姿势挺足，内容全无。",
    "你这理解速度倒不算慢，只是每次都精准地理解错。",
    "你说话像没保存的草稿，混乱、残缺，还非要公开展示。",
    "你那点论据撑不起结论，倒是把你的心虚衬得很明显。",
    "你别忙着摆架子，空洞的观点再端着也不会更有分量。",
    "你这思路像断线的风筝，飘得挺远，就是找不到重点。",
    "你每次自信发言，都像在提醒大家判断力有多重要。",
    "你要是肯花吹嘘自己一半的力气思考，也不至于这么可笑。",
    "你这观点唯一稳定的地方，就是稳定地经不起推敲。",
    "你不是把问题想复杂了，你是从一开始就没想明白。",
    "你先把前后矛盾圆上，再来表演这副胜券在握的样子。",
    "你这番发言没有说服力，倒很有劝人闭嘴的教育意义。",
    "你总能绕开重点直奔笑点，也算一种罕见的精准。",
    "你那点逻辑像临时拼起来的纸桥，碰到事实就当场塌了。",
    "你别再拿情绪冒充证据，声音再大也填不上道理的空缺。",
    "你这思考方式挺环保，一个结论循环使用，从不更新。",
    "你说得越笃定，越显得你对事实到底有多陌生。",
    "你不是没有观点，只是你的观点和常识一直互不认识。",
    "你这场抬杠最大的成果，就是成功证明了自己不讲道理。",
    "你先别急着赢，至少先弄清楚大家到底在讨论什么。",
    "你把话说得这么满，是怕别人看不出你的底气全靠硬撑吗？",
    "你这反驳看似来势汹汹，实际连问题的边都没碰到。",
    "你就像根苦瓜，穿得这么清凉，长得这么败火。",
    "你这么厉害怎么不去青青草原抓羊？",
    "毕竟这也不是一个人贱人爱的社会，你还是收敛一点的好。",
    "你毁了我忍受傻逼的能力。",
    "你妈带你逛街，别人都问：大姐，你这猴多少钱买的？",
    "像你这种人在连续剧里，最多只能活2集，而且还是死尸。",
    "我日你大爷，不不，我好像日了我自己。",
    "一看见你我就知道小学生今天放假了。",
    "你知道你和一盘狗屎的唯一区别是什么吗？就是你没有盘子。",
)


@dataclass
class RetortJob:
    task: asyncio.Task
    starter_id: str
    target_id: str
    umo: str
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
        self._jobs: dict[str, RetortJob] = {}
        self._jobs_lock = asyncio.Lock()
        self._closed = False

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
    def _extract_target(event: AstrMessageEvent) -> str:
        """提取指令消息中第一个有效且非机器人的 At 目标。"""
        self_id = str(event.message_obj.self_id or "").strip().lower()
        invalid_targets = {"", "0", "all", "@all", self_id}
        for component in event.message_obj.message:
            if isinstance(component, Comp.At):
                target = str(getattr(component, "qq", "") or "").strip()
                if target.lower() not in invalid_targets:
                    return target
        return ""

    def _make_chain(self, target_id: str, text: str) -> MessageChain:
        chain = MessageChain()
        if self.mention_target:
            chain.at(target_id, target_id).message(f" {text}")
        else:
            chain.message(text)
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

    async def _retort_loop(self, scope: str, umo: str, target_id: str):
        sent = 0
        failures = 0
        pool = list(RETORTS)
        random.shuffle(pool)
        try:
            while not self._closed and sent < self.max_messages:
                if not pool:
                    pool = list(RETORTS)
                    if self.avoid_duplicates:
                        random.shuffle(pool)
                text = pool.pop() if self.avoid_duplicates else random.choice(RETORTS)
                if await self._send_chain(umo, self._make_chain(target_id, text)):
                    sent += 1
                    failures = 0
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
                        if not self._closed:
                            await self._send_text(umo, "连续发送失败 3 次，任务已自动停止。")
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
            current = asyncio.current_task()
            async with self._jobs_lock:
                job = self._jobs.get(scope)
                if job and job.task is current:
                    self._jobs.pop(scope, None)

    @filter.command("怼他")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def start_retort(self, event: AstrMessageEvent):
        """开始连续回怼被提及的群成员。"""
        target_id = self._extract_target(event)
        if not target_id:
            yield event.plain_result("请使用：/怼他 @某人")
            return

        scope = self._scope_key(event)
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
                        self._retort_loop(scope, event.unified_msg_origin, target_id),
                        name=f"retort_barrage_{scope}",
                    )
                    self._jobs[scope] = RetortJob(
                        task=task,
                        starter_id=str(event.get_sender_id()),
                        target_id=target_id,
                        umo=event.unified_msg_origin,
                    )

        if plugin_closed:
            yield event.plain_result("插件正在停止，请稍后重载后再试。")
            return
        if already_running:
            yield event.plain_result("当前群已有回怼任务，请先发送 /停止怼。")
            return

    @filter.command("停止怼")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def stop_retort(self, event: AstrMessageEvent):
        """停止当前群的连续回怼任务。"""
        scope = self._scope_key(event)
        async with self._jobs_lock:
            job = self._jobs.pop(scope, None)
        if not job or job.task.done():
            yield event.plain_result("当前没有正在运行的回怼任务。")
            return

        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
        yield event.plain_result("已停止回怼。")

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
        yield event.plain_result(
            f"当前正在回怼目标 {job.target_id}；已发送 {job.sent}/{self.max_messages} 条，间隔 {self.send_interval:g} 秒。"
        )

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
