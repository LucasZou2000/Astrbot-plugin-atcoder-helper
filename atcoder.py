"""Read AtCoder's public calendar, contest pages and published results."""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

import aiohttp
from bs4 import BeautifulSoup

BASE_URL = "https://atcoder.jp"
CONTEST_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
HANDLE = re.compile(r"[A-Za-z0-9_]{1,32}")
SERIES = ("abc", "arc", "agc")
BEIJING = timezone(timedelta(hours=8))
FETCH_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError)
Parsed = TypeVar("Parsed")


@dataclass(frozen=True)
class Contest:
    id: str
    name: str
    start: datetime
    end: datetime
    rated_range: str

    @property
    def url(self) -> str:
        return f"{BASE_URL}/contests/{self.id}"

    @property
    def time_text(self) -> str:
        return (
            f"北京时间 {self.start.astimezone(BEIJING):%m-%d %H:%M}"
            f" 至 {self.end.astimezone(BEIJING):%m-%d %H:%M}"
        )


@dataclass(frozen=True)
class Result:
    handle: str
    place: int
    rating: tuple[int, int, int] | None


def parse_calendar(html: str) -> list[Contest]:
    page = BeautifulSoup(html, "html.parser")
    sections = page.select(
        "#contest-table-upcoming, #contest-table-action, #contest-table-recent"
    )
    if not sections:
        raise ValueError("AtCoder 赛历页面缺少比赛表")
    contests = {}
    for row in page.select(
        "#contest-table-upcoming tbody tr, #contest-table-action tbody tr, "
        "#contest-table-recent tbody tr"
    ):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4:
            raise ValueError("AtCoder 赛历行缺少字段")
        link = cells[1].select_one('a[href^="/contests/"]')
        start_tag = cells[0].find("time")
        if not link or not start_tag:
            raise ValueError("AtCoder 赛历行缺少比赛链接或时间")
        contest_id = link["href"].removeprefix("/contests/")
        if not CONTEST_ID.fullmatch(contest_id):
            continue
        start = datetime.fromisoformat(start_tag.get_text(strip=True))
        if start.tzinfo is None:
            raise ValueError("AtCoder 赛历时间缺少时区")
        hours, minutes = map(int, cells[2].get_text(strip=True).split(":"))
        contests[contest_id] = Contest(
            contest_id,
            link.get_text(" ", strip=True),
            start,
            start + timedelta(hours=hours, minutes=minutes),
            cells[3].get_text(" ", strip=True),
        )
    return sorted(contests.values(), key=lambda contest: contest.start)


def registration_open(html: str, contest_id: str) -> bool:
    page = BeautifulSoup(html, "html.parser")
    form = page.select_one(f'form[action="/contests/{contest_id}/register"]')
    return bool(form and form.select_one("button:not([disabled])"))


def parse_results(payload: object, contest_id: str) -> dict[str, Result]:
    if not isinstance(payload, list):
        raise ValueError("AtCoder 结果页面未返回结果列表")
    results = {}
    for row in payload:
        if (
            not isinstance(row, dict)
            or row.get("ContestScreenName") != f"{contest_id}.contest.atcoder.jp"
            or not isinstance(row.get("UserScreenName"), str)
            or type(row.get("Place")) is not int
            or type(row.get("IsRated")) is not bool
        ):
            raise ValueError("AtCoder 结果行缺少有效的场次、账号或名次")
        rating = None
        if row["IsRated"]:
            old, new, performance = (row.get(key) for key in ("OldRating", "NewRating", "Performance"))
            if any(type(value) is not int for value in (old, new, performance)):
                raise ValueError("AtCoder 计分结果缺少 Rating 或 Performance")
            rating = old, new, performance
        if row["Place"] > 0:
            result = Result(row["UserScreenName"], row["Place"], rating)
            results[result.handle.casefold()] = result
    return results


def summary(contest_id: str, title: str, players: list[str], results: dict[str, Result]) -> str:
    matched = sorted(
        (results[player.casefold()] for player in players if player.casefold() in results),
        key=lambda result: result.place,
    )
    lines = [f"【AtCoder 赛后总结】{title}", f"关注成员 {len(players)} 人，结果页有名次 {len(matched)} 人"]
    for result in matched:
        line = f"#{result.place} {result.handle}"
        if result.rating is None:
            lines.append(f"{line}｜未计 Rating")
        else:
            old, new, performance = result.rating
            lines.append(f"{line}｜Rating {old} → {new}（{new - old:+d}）｜Performance {performance}")
    missing = [player for player in players if player.casefold() not in results]
    if missing:
        lines.append(f"结果页无名次：{'、'.join(missing)}")
    lines.append(f"{BASE_URL}/contests/{contest_id}/results")
    return "\n".join(lines)


class AtCoder:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.cache = {}

    async def _get(self, path: str, ttl: int, parse: Callable[..., Parsed]) -> Parsed:
        async with self.lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            self.cache = {key: value for key, value in self.cache.items() if value[0] > now}
            if path in self.cache:
                _, result, error = self.cache[path]
                if error is not None:
                    raise ValueError(error)
                return result
            if now < self.next_request:
                if self.next_request - now > 2:
                    raise ValueError("AtCoder 请求限流，稍后重试")
                await asyncio.sleep(self.next_request - now)
            try:
                async with self.session.get(BASE_URL + path, allow_redirects=False) as response:
                    self.next_request = loop.time() + (300 if response.status == 429 else 2)
                    if response.status != 200:
                        raise ValueError(f"AtCoder {path} 返回 HTTP {response.status}")
                    data = await response.json() if path.endswith("/json") else await response.text()
                result = parse(data)
            except FETCH_ERRORS as error:
                self.cache[path] = (loop.time() + 60, None, str(error) or type(error).__name__)
                raise
            self.cache[path] = (loop.time() + ttl, result, None)
            return result

    async def calendar(self) -> list[Contest]:
        return await self._get("/contests", 300, parse_calendar)

    async def can_register(self, contest_id: str) -> bool:
        return await self._get(
            f"/contests/{contest_id}", 600, lambda html: registration_open(html, contest_id)
        )

    async def results(self, contest_id: str) -> dict[str, Result]:
        return await self._get(
            f"/contests/{contest_id}/results/json", 300, lambda data: parse_results(data, contest_id)
        )
