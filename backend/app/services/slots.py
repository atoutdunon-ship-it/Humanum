"""Calcul des créneaux libres — fonction pure, sans base ni réseau (entièrement testable).

Entrées : plages hebdomadaires en heure locale du coach, occupations (UTC), règles du type de rendez-vous.
Un créneau [t, t+durée) est proposé si :
- il tient entièrement dans une plage de disponibilité (heure locale, changements d'heure gérés par zoneinfo) ;
- il respecte le délai de prévenance et l'horizon de réservation ;
- [t − battement avant, t + durée + battement après) ne chevauche aucune occupation.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class Rule:
    weekday: int        # 0 = lundi
    start_minute: int
    end_minute: int


@dataclass(frozen=True, slots=True)
class Policy:
    timezone: str
    duration: int
    buffer_before: int = 0
    buffer_after: int = 0
    step: int = 30
    min_notice: int = 240
    max_days_ahead: int = 120


Interval = tuple[datetime, datetime]


def _local(d: date, minute: int, tz: ZoneInfo) -> datetime:
    if minute >= 1440:
        return datetime.combine(d + timedelta(days=1), time(0), tz)
    return datetime.combine(d, time(minute // 60, minute % 60), tz)


def _merge(busy: Iterable[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a, b in sorted(busy):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def free_slots(rules: Iterable[Rule], busy: Iterable[Interval], policy: Policy, *, first_day: date, days: int,
               now: datetime) -> dict[date, list[datetime]]:
    tz = ZoneInfo(policy.timezone)
    blocks = _merge(busy)
    earliest = now + timedelta(minutes=policy.min_notice)
    latest = now + timedelta(days=policy.max_days_ahead)
    dur, step = timedelta(minutes=policy.duration), timedelta(minutes=policy.step)
    before, after = timedelta(minutes=policy.buffer_before), timedelta(minutes=policy.buffer_after)
    by_day: dict[int, list[Rule]] = {}
    for r in rules:
        by_day.setdefault(r.weekday, []).append(r)

    result: dict[date, list[datetime]] = {}
    for i in range(days):
        d = first_day + timedelta(days=i)
        found: set[datetime] = set()
        for r in by_day.get(d.weekday(), []):
            t, end = _local(d, r.start_minute, tz), _local(d, r.end_minute, tz)
            while t + dur <= end:
                tu = t.astimezone(UTC)
                if earliest <= tu <= latest and not _overlaps(tu - before, tu + dur + after, blocks):
                    found.add(tu)
                t = (t.astimezone(UTC) + step).astimezone(tz)  # pas en temps réel : correct aux changements d'heure
        if found:
            result[d] = sorted(found)
    return result


def _overlaps(a: datetime, b: datetime, blocks: list[Interval]) -> bool:
    return any(x < b and a < y for x, y in blocks)


def is_free(start: datetime, rules: Iterable[Rule], busy: Iterable[Interval], policy: Policy, now: datetime) -> bool:
    """Revalidation au moment de réserver (le créneau a pu être pris entre l'affichage et le clic)."""
    local_day = start.astimezone(ZoneInfo(policy.timezone)).date()
    slots = free_slots(rules, busy, policy, first_day=local_day, days=1, now=now)
    return start.astimezone(UTC) in slots.get(local_day, [])
