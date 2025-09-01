from __future__ import annotations

import json
import re
from typing import Any, Iterable

import requests

BASE = "https://abiturient-api.vlsu.ru"

# Один Session с нужными заголовками — как у фронта
S = requests.Session()
S.headers.update({
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://student.vlsu.ru",
    "Referer": "https://student.vlsu.ru/",
    "User-Agent": "Mozilla/5.0",
})


# ------------ Вспомогательное ------------

def _json(r: requests.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return json.loads(r.text)


# ------------ Справочники ------------

def get_institutes() -> list[dict]:
    r = S.get(f"{BASE}/api/catalogs/GetInstitutes", timeout=30)
    r.raise_for_status()
    return _json(r)


def find_institute_id(name_substr: str) -> str | None:
    name_substr = (name_substr or "").lower()
    for it in get_institutes():
        if name_substr in str(it.get("Text", "")).lower():
            return it.get("Value")
    return None


# ------------ Группы ------------

def get_groups(institute_id: str, form: int = 0, debug: bool = False) -> list[dict]:
    """
    Получить группы по институту.
    form: 0=очная, 1=заочная, 2=очно‑заочная
    Возвращает список вида: [{"id":"..","name":"КП-125","course":"1 курс"}, ...]
    """
    url = f"{BASE}/api/student/GetStudGroups"
    # Бэкенд ждёт именно такие ключи!
    body = {"Institut": institute_id, "WFormed": form}

    r = S.post(url, json=body, timeout=30)
    if debug:
        print("[DEBUG] POST", url, "json", body, "status", r.status_code, "len", len(r.text))
    r.raise_for_status()
    raw = _json(r)
    return _normalize_groups(raw)


def _normalize_groups(raw) -> list[dict]:
    out: list[dict] = []
    if isinstance(raw, list):
        for g in raw:
            if isinstance(g, dict):
                out.append({
                    "id": g.get("Nrec") or g.get("Value") or g.get("Id") or g.get("ID"),
                    "name": g.get("Name") or g.get("Text"),
                    "course": g.get("Course") or g.get("Kurs") or g.get("CourseNumber"),
                })
    return out


def find_group(groups: Iterable[dict], query: str) -> dict | None:
    q = (query or "").lower().replace(" ", "")
    for g in groups:
        if q == str(g["name"]).lower().replace(" ", ""):
            return g
    for g in groups:
        if q in str(g["name"]).lower().replace(" ", ""):
            return g
    return None


# ------------ Расписание и «сейчас» ------------

def get_schedule(
    group_id: str,
    week_type: int | None = None,            # None/0 — все недели; 1 — числитель; 2 — знаменатель
    days: str = "1,2,3,4,5,6",               # какие дни вернуть
    raw: bool = False,
    debug: bool = False,
):
    """
    Тянет расписание через POST /api/student/GetGroupSchedule
    Тело запроса строго:
      {"Nrec": "<ID группы>", "WeekType": 0|1|2, "WeekDays": "1,2,3,4,5,6"}
    """
    url = f"{BASE}/api/student/GetGroupSchedule"
    body = {
        "Nrec": group_id,
        "WeekType": 0 if week_type in (None, 0) else int(week_type),
        "WeekDays": days,
    }
    r = S.post(url, json=body, timeout=30)
    if debug:
        print("[DEBUG] POST", url, "json", body, "status", r.status_code, "len", len(r.text))
    r.raise_for_status()
    data = _json(r)
    return data if raw else normalize_schedule(data)


def get_group_current_info(group_id: str, debug: bool = False) -> dict:
    """
    Тянет карточку «сейчас/следующая» через POST /api/student/GetGroupCurrentInfo
    Тело: {"Nrec": "<ID>"}
    """
    url = f"{BASE}/api/student/GetGroupCurrentInfo"
    body = {"Nrec": group_id}
    r = S.post(url, json=body, timeout=30)
    if debug:
        print("[DEBUG] POST", url, "json", body, "status", r.status_code, "len", len(r.text))
    r.raise_for_status()
    return _json(r)


# ------------ Нормализация ответа ------------

def normalize_week_type(val) -> str:
    """Свести тип недели к: all | odd | even."""
    if val is None:
        return "all"
    if isinstance(val, (int, float)):
        v = int(val)
        if v == 1: return "odd"
        if v == 2: return "even"
        return "all"
    s = str(val).strip().lower()
    if "числ" in s or "odd" in s: return "odd"
    if "знам" in s or "even" in s: return "even"
    if s in {"0", "all", ""}: return "all"
    return "all"


def _pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


# Времена пар (как на скриншоте)
PAIR_TIMES = {
    1: ("08:30", "10:00"),
    2: ("10:20", "11:50"),
    3: ("12:10", "13:40"),
    4: ("14:00", "15:30"),
    5: ("15:50", "17:20"),
    6: ("17:40", "19:10"),
    7: ("19:30", "21:00"),
}

DAY_MAP_RU = {
    "понедельник": 1,
    "вторник": 2,
    "среда": 3,
    "четверг": 4,
    "пятница": 5,
    "суббота": 6,
    "воскресенье": 7,
}


def _looks_like_room(s: str) -> bool:
    s = (s or "").strip()
    return bool(re.search(r"\d", s)) or "-" in s


def _looks_like_teacher(s: str) -> bool:
    s = (s or "")
    return ("." in s) and (" " in s)


def _parse_cell_text(s: str) -> tuple[str | None, str | None, str | None, str | None]:
    """
    "лк, 529а-3, Филатов Д.О., Общая психология"
    "пр, Физическая культура и спорт, поток"
    -> (kind, room, teacher, title)
    """
    if not s:
        return (None, None, None, None)
    parts = [p.strip() for p in s.split(",") if p.strip()]
    kind = None
    room = None
    teacher = None
    used = set()

    if parts and len(parts[0]) <= 3:
        kind = parts[0]
        used.add(0)

    for i, p in enumerate(parts):
        if i in used: continue
        if room is None and _looks_like_room(p):
            room = p
            used.add(i)
            break

    for i, p in enumerate(parts):
        if i in used: continue
        if teacher is None and _looks_like_teacher(p):
            teacher = p
            used.add(i)
            break

    title_parts = [p for i, p in enumerate(parts) if i not in used]
    title = ", ".join(title_parts) if title_parts else None
    return (kind, room, teacher, title)


def _normalize_special_day_array(raw: list) -> list[dict]:
    """
    Формат:
      [{type:"Lessons", name:"Понедельник", n1:"...", z1:"...", ...}, ...]
    n* — числитель (odd), z* — знаменатель (even)
    """
    out: list[dict] = []
    for day_obj in raw:
        if not isinstance(day_obj, dict): continue
        if day_obj.get("type") != "Lessons": continue
        day_name = str(day_obj.get("name", "")).strip().lower()
        day_num = DAY_MAP_RU.get(day_name)
        if not day_num: continue

        for idx in range(1, 8):
            for key, week in (("n", "odd"), ("z", "even")):
                cell = (day_obj.get(f"{key}{idx}") or "").strip()
                if not cell: continue
                kind, room, teacher, title = _parse_cell_text(cell)
                start, end = PAIR_TIMES.get(idx, (None, None))
                if not any([title, room, teacher, kind]): continue
                out.append({
                    "day": day_num,
                    "start": start,
                    "end": end,
                    "title": title,
                    "teacher": teacher,
                    "room": room,
                    "kind": kind,
                    "week": week,
                })
    return out


def normalize_schedule(raw: dict | list) -> list[dict]:
    """
    Итоговый формат записи:
    {
      "day": 1..7, "start": "08:30", "end": "10:00",
      "title": "...", "teacher": "...", "room": "...",
      "kind": "лк/пр/лб/—", "week": "all|odd|even"
    }
    """
    if isinstance(raw, list) and any(isinstance(x, dict) and ("n1" in x or "z1" in x) for x in raw):
        return _normalize_special_day_array(raw)

    lessons: list[dict] = []

    def push(item: dict, ctx_day=None, ctx_week=None):
        if not isinstance(item, dict):
            return
        week = normalize_week_type(
            _pick(item, "WeekType", "Week", "WeekMode", "TypeWeek", default=ctx_week)
        )
        day = _pick(item, "Day", "DayOfWeek", "WeekDay", default=ctx_day)
        start = _pick(item, "Start", "TimeStart", "Begin", "From", "StartTime")
        end   = _pick(item, "End", "TimeEnd", "Finish", "To", "EndTime")
        title = _pick(item, "Title", "Discipline", "Subject", "Name", "Lesson")
        teacher = _pick(item, "Teacher", "Lecturer", "Professor", "Prepod", "TeacherName")
        room = _pick(item, "Room", "Audience", "Auditory", "Classroom", "Cabinet", "Aud")
        kind = _pick(item, "Kind", "Type", "LessonType", "Format")

        if not any([title, room, teacher, start, end]):
            return

        try:
            day_val = int(day) if str(day).isdigit() else day
        except Exception:
            day_val = day

        lessons.append({
            "day": day_val,
            "start": start,
            "end": end,
            "title": title,
            "teacher": teacher,
            "room": room,
            "kind": kind,
            "week": week,
        })

    def walk(node, ctx_day=None, ctx_week=None):
        if isinstance(node, list):
            for x in node:
                walk(x, ctx_day, ctx_week)
            return
        if not isinstance(node, dict):
            return

        if any(k in node for k in ("Start", "TimeStart", "Begin", "From", "StartTime")) or \
           any(k in node for k in ("Title", "Discipline", "Subject", "Lesson", "Name")):
            push(node, ctx_day, ctx_week)

        new_day = _pick(node, "Day", "DayOfWeek", "WeekDay", default=ctx_day)
        new_week = _pick(node, "WeekType", "Week", "WeekMode", "TypeWeek", default=ctx_week)

        for key in ("Days", "DayItems", "Schedule", "Lessons", "Pairs", "Items"):
            if key in node:
                walk(node[key], new_day, new_week)

        for key, w in (("All", "all"), ("Numerator", "odd"), ("Odd", "odd"),
                       ("Denominator", "even"), ("Even", "even")):
            if key in node:
                walk(node[key], ctx_day, w)

        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v, new_day, new_week)

    walk(raw)

    seen = set()
    uniq: list[dict] = []
    for l in lessons:
        key = (l["day"], l["start"], l["end"], l["title"], l["teacher"], l["room"], l["kind"], l["week"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(l)
    return uniq
