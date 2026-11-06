import asyncio
import json
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .atcoder import CONTEST_ID, FETCH_ERRORS, HANDLE, SERIES, AtCoder, Contest, summary

PLUGIN_NAME = "astrbot_plugin_atcoder_helper"
ANNOUNCE_WINDOW = timedelta(days=7)
REGISTRATION_WINDOW = timedelta(hours=24)
START_WINDOW = timedelta(minutes=10)
RESULT_DELAY = timedelta(minutes=20)
RESULT_WINDOW = timedelta(hours=24)
DELIVERY_RETENTION = 30 * 86400


class AtCoderHelper(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.task = None

    async def initialize(self):
        self.state_path = StarTools.get_data_dir(PLUGIN_NAME) / "deliveries.json"
        self.sent = {}
        self.state_dirty = False
        if self.state_path.exists():
            self.sent = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(self.sent, dict) or any(
                type(timestamp) is not int for timestamp in self.sent.values()
            ):
                raise ValueError("AtCoder 推送记录格式错误")
        self.sent = {
            key: timestamp
            for key, timestamp in self.sent.items()
            if timestamp > time.time() - DELIVERY_RETENTION
        }
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=True,
        )
        self.atcoder = AtCoder(self.session)
        self.task = asyncio.create_task(self._run())

    async def terminate(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            await self.session.close()

    def _players(self) -> list[str]:
        players = {}
        for value in self.config.get("players", []):
            handle = value.strip()
            if handle and not HANDLE.fullmatch(handle):
                raise ValueError(f"AtCoder 用户名格式错误：{handle}")
            if handle:
                players.setdefault(handle.casefold(), handle)
        return list(players.values())

    def _series(self) -> set[str]:
        series = {value.strip().lower() for value in self.config.get("series", SERIES)}
        if not series.issubset(SERIES):
            raise ValueError("比赛系列只支持 abc、arc、agc")
        return series

    async def _run(self):
        while True:
            try:
                targets = list(dict.fromkeys(
                    value.strip() for value in self.config.get("targets", []) if value.strip()
                ))
                if self.config.get("enabled", False) and targets:
                    await self._poll(targets, self._players(), self._series())
            except FETCH_ERRORS as error:
                logger.warning(f"AtCoder 助手轮询失败：{error}")
            except Exception:
                logger.exception("AtCoder 助手处理推送时出错")
            await asyncio.sleep(30)

    async def _poll(self, targets: list[str], players: list[str], series: set[str]):
        if self.state_dirty:
            self._save_deliveries()
        if not series:
            return
        contests = await self.atcoder.calendar()
        for contest in contests:
            if contest.id.rstrip("0123456789") not in series:
                continue
            try:
                await self._process(contest, targets, players)
            except FETCH_ERRORS as error:
                logger.warning(f"AtCoder {contest.id} 暂时无法获取数据：{error}")

    async def _process(self, contest: Contest, targets: list[str], players: list[str]):
        now = datetime.now(timezone.utc)
        until_start = contest.start - now
        if timedelta(0) < until_start <= ANNOUNCE_WINDOW:
            await self._notify(
                contest, "announcement", targets,
                f"【AtCoder 赛程】{contest.name}\n{contest.time_text}\n"
                f"Rated 范围 {contest.rated_range}\n{contest.url}",
            )
            if (
                timedelta(0) < until_start <= REGISTRATION_WINDOW
                and any(self._pending(contest, "registration", targets))
                and await self.atcoder.can_register(contest.id)
                and datetime.now(timezone.utc) < contest.start
            ):
                await self._notify(
                    contest, "registration", targets,
                    f"【AtCoder 报名提醒】{contest.name}\n{contest.time_text}\n"
                    f"报名已开放，登录 AtCoder 后在比赛页点击 Register。\n{contest.url}",
                )
        elif contest.start <= now < min(contest.start + START_WINDOW, contest.end):
            await self._notify(
                contest, "start", targets,
                f"【AtCoder 开赛】{contest.name}\n{contest.time_text}\n{contest.url}",
            )
        elif players and RESULT_DELAY <= now - contest.end <= RESULT_WINDOW:
            if any(self._pending(contest, "results", targets)):
                results = await self.atcoder.results(contest.id)
                if results:
                    await self._notify(
                        contest, "results", targets,
                        summary(contest.id, contest.name, players, results),
                    )

    def _pending(self, contest: Contest, kind: str, targets: list[str]):
        revision = "" if kind == "results" else int(contest.start.timestamp())
        for target in targets:
            key = json.dumps([target, contest.id, kind, revision], ensure_ascii=False)
            if key not in self.sent:
                yield target, key

    async def _notify(self, contest: Contest, kind: str, targets: list[str], message: str):
        for target, key in self._pending(contest, kind, targets):
            try:
                delivered = await asyncio.wait_for(
                    self.context.send_message(target, MessageChain().message(message)),
                    timeout=15,
                )
            except Exception as error:
                logger.warning(f"AtCoder {contest.id} {kind} 推送失败：{error}")
                continue
            if not delivered:
                logger.warning(f"AtCoder {contest.id} {kind} 推送失败，未找到对应平台：{target}")
                continue
            now = int(time.time())
            self.sent = {
                previous: timestamp
                for previous, timestamp in self.sent.items()
                if timestamp > now - DELIVERY_RETENTION
            }
            self.sent[key] = now
            self.state_dirty = True
            self._save_deliveries()

    def _save_deliveries(self):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.sent, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)
        self.state_dirty = False

    @filter.command_group("atcoder")
    def atcoder_group(self):
        """AtCoder 比赛助手。"""

    @atcoder_group.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """查看 AtCoder 助手的用法。"""
        yield event.plain_result(
            "/atcoder list 查看即将开赛的比赛\n"
            "/atcoder results <比赛 ID> 查看关注成员的赛后总结\n"
            "管理员可在 AstrBot 插件配置中设置关注成员及推送会话。"
        )

    @atcoder_group.command("list")
    async def show_calendar(self, event: AstrMessageEvent):
        """查看最近五场即将开赛的比赛。"""
        try:
            series = self._series()
            now = datetime.now(timezone.utc)
            contests = [
                contest for contest in await self.atcoder.calendar()
                if contest.start > now and contest.id.rstrip("0123456789") in series
            ][:5]
        except FETCH_ERRORS as error:
            logger.warning(f"AtCoder 赛程查询失败：{error}")
            yield event.plain_result("暂时无法获取 AtCoder 赛程，请稍后再试。")
            return
        if not contests:
            yield event.plain_result("所选系列暂无即将开赛的比赛。")
            return
        yield event.plain_result("\n\n".join(
            f"{contest.name}（{contest.id}）\n{contest.time_text}\n{contest.url}"
            for contest in contests
        ))

    @atcoder_group.command("results")
    async def show_results(self, event: AstrMessageEvent, contest_id: str = ""):
        """查询指定比赛中关注成员的公开结果。"""
        contest_id = contest_id.lower()
        if not CONTEST_ID.fullmatch(contest_id):
            yield event.plain_result("用法：/atcoder results abc482")
            return
        try:
            players = self._players()
            if not players:
                yield event.plain_result("请先在插件配置中添加关注的 AtCoder 用户名。")
                return
            results = await self.atcoder.results(contest_id)
        except FETCH_ERRORS as error:
            logger.warning(f"AtCoder {contest_id} 结果查询失败：{error}")
            yield event.plain_result("暂时无法读取比赛结果，请核对比赛 ID 或稍后再试。")
            return
        if not results:
            yield event.plain_result("AtCoder 尚未返回这场比赛的名次。")
            return
        yield event.plain_result(summary(contest_id, contest_id, players, results))
