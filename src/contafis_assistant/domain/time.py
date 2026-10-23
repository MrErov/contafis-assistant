"""Reloj inyectable y ventanas de tiempo en America/Caracas (RF-027, RNF-011)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

CARACAS = ZoneInfo("America/Caracas")


def to_caracas(value: datetime) -> datetime:
    """Convierte un instante a Caracas; un naive se interpreta como UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CARACAS)


def today_caracas(now: datetime | None = None) -> date:
    """Fecha de negocio en Caracas calculada desde un reloj UTC inyectable."""
    return to_caracas(now or datetime.now(timezone.utc)).date()


class Clock:
    """Reloj inyectable. Si `fixed` está presente, el tiempo es determinista."""

    def __init__(self, fixed: datetime | None = None) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed or datetime.now(timezone.utc)

    def today_caracas(self) -> date:
        return today_caracas(self.now())


def day_window(day: date, tz: ZoneInfo = CARACAS) -> tuple[datetime, datetime]:
    """Ventana semiabierta de un día: [00:00, siguiente 00:00)."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def week_window(day: date, tz: ZoneInfo = CARACAS) -> tuple[datetime, datetime]:
    """Ventana semiabierta de la semana: [lunes 00:00, lunes siguiente 00:00)."""
    monday = day - timedelta(days=day.weekday())
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = datetime.combine(monday + timedelta(days=7), time.min, tzinfo=tz)
    return start, end


def format_date(value: date | None) -> str | None:
    """Formato de negocio es-VE: dd/mm/aaaa."""
    return value.strftime("%d/%m/%Y") if value is not None else None


def format_datetime(value: datetime | None) -> str | None:
    """Formato de negocio es-VE para fecha y hora en Caracas."""
    if value is None:
        return None
    return to_caracas(value).strftime("%d/%m/%Y %H:%M")
