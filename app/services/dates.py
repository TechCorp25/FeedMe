"""Turning stored instants into the dates a customer is shown.

Every timestamp in this application is UTC and stays UTC — that is the
storage rule, and nothing here changes it. What a customer reads is a
different thing: a date in the kitchen's own timezone. For the ten hours
between Melbourne midnight and UTC midnight the two disagree, which is
long enough for an order placed at nine this morning to be rendered as
yesterday's.

`services/checkout.py` already applies this rule on the way *in*, when it
validates `requested_for` against the kitchen's date rather than UTC's.
This is the same rule on the way out.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

logger = logging.getLogger(__name__)

DATE_FORMAT = "%-d %B %Y"


def business_zone(timezone_name: str) -> ZoneInfo | None:
    """The kitchen's zone, or None when the name does not resolve.

    A misconfigured timezone is worth fixing and is not worth a 500 on a
    page the customer is trying to read, so it is logged and the instant
    is rendered as stored.
    """
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.error(
            "unknown business timezone; rendering the stored instant",
            extra={"timezone": timezone_name},
        )
        return None


def to_business_date(moment: datetime | date | None) -> date | None:
    """The calendar date this instant falls on, in the kitchen's zone.

    A plain `date` is returned unchanged: `requested_for` is already a
    local date the customer chose, and shifting it by a timezone would
    move a date nobody expressed in UTC in the first place.
    """
    if moment is None:
        return None
    if not isinstance(moment, datetime):
        return moment

    zone = business_zone(current_app.config["BUSINESS_TIMEZONE"])
    if zone is None or moment.tzinfo is None:
        # A naive datetime has no offset to convert from. Guessing one
        # would be inventing information; it is rendered as stored.
        return moment.date()
    return moment.astimezone(zone).date()


def format_business_date(moment: datetime | date | None) -> str:
    """`8 September 2026`, in the kitchen's timezone. Empty when absent."""
    local = to_business_date(moment)
    return "" if local is None else local.strftime(DATE_FORMAT)


def business_today() -> date:
    """Today in the kitchen's timezone.

    The prep sheet is organised by the day the kitchen is working, and
    for the ten hours between Melbourne midnight and UTC midnight
    `date.today()` on a UTC host is yesterday — which would open the
    sheet on a day whose orders have already gone out.
    """
    zone = business_zone(current_app.config["BUSINESS_TIMEZONE"])
    moment = datetime.now(timezone.utc)
    return moment.date() if zone is None else moment.astimezone(zone).date()
