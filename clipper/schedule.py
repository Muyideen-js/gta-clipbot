"""Posting schedule kept in manifests/schedule.json, so runs never double-book a slot.

Shorts are spread evenly across a daily posting window; the long best-of gets
its own evening slot. Times are worked out in POST_TZ (default Africa/Lagos)
and sent to YouTube as UTC publishAt, so YouTube flips each video public on time.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

GAP = dt.timedelta(minutes=20)        # minimum distance between any two posts
LEAD = dt.timedelta(minutes=30)       # never schedule closer than this to now


class Schedule:
    def __init__(self, path: str):
        self.path = path
        self.tz = ZoneInfo(os.environ.get("POST_TZ") or "Africa/Lagos")
        self.per_day = int(os.environ.get("SHORTS_PER_DAY") or 6)
        self.start = float(os.environ.get("POST_START_HOUR") or 10)
        self.end = float(os.environ.get("POST_END_HOUR") or 22)
        self.long_hour = float(os.environ.get("LONG_POST_HOUR") or 19)
        # POST_INTERVAL_MIN=30 -> a Short every 30 minutes inside the window, instead of N per day.
        self.interval = float(os.environ.get("POST_INTERVAL_MIN") or 0)
        if self.interval:
            self.per_day = int((self.end - self.start) * 60 // self.interval) + 1
        self.lead = dt.timedelta(minutes=float(os.environ.get("POST_LEAD_MIN") or 30))
        self.taken: list[dict] = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.taken = json.load(fh).get("slots", [])
        now = dt.datetime.now(dt.timezone.utc)
        # Forget slots older than two days.
        self.taken = [s for s in self.taken
                      if dt.datetime.fromisoformat(s["at"].replace("Z", "+00:00")) > now - dt.timedelta(days=2)]

    def _busy(self, t: dt.datetime) -> bool:
        return any(abs(dt.datetime.fromisoformat(s["at"].replace("Z", "+00:00")) - t) < GAP for s in self.taken)

    def _count(self, day: dt.date, kind: str) -> int:
        return sum(1 for s in self.taken if s["kind"] == kind and
                   dt.datetime.fromisoformat(s["at"].replace("Z", "+00:00")).astimezone(self.tz).date() == day)

    @staticmethod
    def _at(day: dt.date, hour: float, tz) -> dt.datetime:
        h = int(hour)
        m = int(round((hour - h) * 60))
        return dt.datetime(day.year, day.month, day.day, h, min(m, 59), tzinfo=tz)

    def next(self, kind: str = "short", label: str = "") -> dt.datetime:
        now = dt.datetime.now(self.tz)
        for d in range(120):
            day = (now + dt.timedelta(days=d)).date()
            if kind == "long":
                if self._count(day, "long") >= 1:
                    continue
                hours = [self.long_hour, self.long_hour + 0.5, self.long_hour + 1]
            else:
                if self._count(day, "short") >= self.per_day:
                    continue
                n = self.per_day
                span = max(self.end - self.start, 0)
                if self.interval:
                    hours = [self.start + i * self.interval / 60 for i in range(n)]
                else:
                    hours = [self.start + (span * i / (n - 1) if n > 1 else 0) for i in range(n)]
            for h in hours:
                t = self._at(day, h, self.tz)
                if t < now + self.lead or self._busy(t.astimezone(dt.timezone.utc)):
                    continue
                self.taken.append({"at": t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                                   "kind": kind, "label": label[:80]})
                return t
        raise RuntimeError("No free slot in the next 120 days")

    def release(self, t: dt.datetime) -> None:
        """Give a slot back (upload failed after booking)."""
        iso = t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        self.taken = [s for s in self.taken if s["at"] != iso]

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.taken.sort(key=lambda s: s["at"])
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"tz": str(self.tz), "slots": self.taken}, fh, indent=1, ensure_ascii=False)


def utc_iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human(t: dt.datetime, tz) -> str:
    return t.astimezone(tz).strftime("%a %d %b, %H:%M") + f" ({tz.key.split('/')[-1].replace('_', ' ')} time)"
