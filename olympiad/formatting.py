"""Russian labels and readable Moscow dates for HTML pages."""
from datetime import datetime, timedelta, timezone

MOSCOW = timezone(timedelta(hours=3))
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def statement_text(value):
    note = "\n\nДля приведённых входных данных запишите результат. Программу отправлять не нужно."
    return value.removesuffix(note)


def ru_plural(value, one, few, many):
    number = abs(int(value))
    if 11 <= number % 100 <= 14:
        return many
    if number % 10 == 1:
        return one
    if 2 <= number % 10 <= 4:
        return few
    return many


def datetime_ru(value):
    if not value:
        return "—"
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(MOSCOW)
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}, {moment:%H:%M}"
