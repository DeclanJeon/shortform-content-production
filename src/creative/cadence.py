"""Seoul production windows; missed windows never create catch-up entitlements."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

SEOUL: Final = ZoneInfo('Asia/Seoul')


@dataclass(frozen=True, slots=True)
class CadenceConfigurationError(ValueError):
    reason: Literal['interval', 'timezone', 'anchor']

    def __str__(self) -> str:
        return 'production_cadence_invalid:' + self.reason


@dataclass(frozen=True, slots=True)
class ProductionWindow:
    key: str
    starts_at: datetime
    next_at: datetime
    admitted_time: bool


def production_window(now: datetime, interval_minutes: int, anchor: str = '') -> ProductionWindow:
    """Parse cadence configuration once and return only the current window."""
    if type(interval_minutes) is not int or interval_minutes <= 0 or 1440 % interval_minutes:
        raise CadenceConfigurationError('interval')
    if now.tzinfo is None or now.utcoffset() is None:
        raise CadenceConfigurationError('timezone')
    try:
        origin = datetime.fromisoformat(anchor) if anchor else datetime(1970, 1, 1, tzinfo=SEOUL)
    except ValueError as exc:
        raise CadenceConfigurationError('anchor') from exc
    if origin.tzinfo is None or origin.utcoffset() is None:
        raise CadenceConfigurationError('timezone')
    origin = origin.astimezone(SEOUL)
    local = now.astimezone(SEOUL)
    period = timedelta(minutes=interval_minutes)
    start = origin if local < origin else origin + ((local - origin) // period) * period
    return ProductionWindow(start.isoformat(), start, start + period, local >= origin)
