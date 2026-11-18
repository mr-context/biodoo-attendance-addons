"""Fuseaux horaires : l'utilitaire unique de biodoo.

Odoo stocke les datetimes en UTC naïf ; les employés vivent en heure locale
(Africa/Algiers par défaut). Toute conversion jour local ↔ UTC passe par ici,
pour qu'un pointage de 0 h 30 heure d'Alger tombe toujours dans le bon jour
et le bon mois (il est 23 h 30 la veille en UTC).
"""
from datetime import datetime, time, timedelta

import pytz

DEFAULT_TZ = 'Africa/Algiers'


def tz_of(record):
    """Fuseau d'un employé, d'un utilisateur ou d'une société (à défaut, Alger)."""
    name = None
    for attr in ('tz', 'resource_calendar_id'):
        value = getattr(record, attr, None) if record else None
        if isinstance(value, str) and value:
            name = value
            break
        if value and getattr(value, 'tz', None):
            name = value.tz
            break
    if not name and record and getattr(record, 'company_id', None):
        name = record.company_id.partner_id.tz
    try:
        return pytz.timezone(name or DEFAULT_TZ)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(DEFAULT_TZ)


def utc_bounds(day_from, day_to, tz):
    """[début du jour local `day_from`, début du jour local suivant `day_to`[
    en datetimes UTC naïfs, prêts pour un domaine Odoo (>= début, < fin)."""
    start = tz.localize(datetime.combine(day_from, time.min)).astimezone(pytz.utc)
    end = tz.localize(datetime.combine(day_to + timedelta(days=1), time.min)).astimezone(pytz.utc)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def local_date(dt_utc, tz):
    """Jour local d'un datetime UTC naïf."""
    return pytz.utc.localize(dt_utc).astimezone(tz).date()
