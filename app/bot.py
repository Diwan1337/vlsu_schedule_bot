import os
import asyncio
import aiosqlite
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH   = os.getenv("DB_PATH", "data/vlsu_schedule.db")
TZ        = os.getenv("TZ", "Europe/Moscow")

# -------- utils --------
DAY_NAMES_RU = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
    7: "Воскресенье",
}
DAY_SHORT = {1:"Пн",2:"Вт",3:"Ср",4:"Чт",5:"Пт",6:"Сб",7:"Вс"}

def tznow() -> datetime:
    return datetime.now(ZoneInfo(TZ))

SEMESTER_START = date(2025, 9, 1)

SPECIAL_PERIODS = [
    (date(2025, 9, 29), date(2025, 10, 11), "rc"),
    (date(2025, 11, 10), date(2025, 11, 22), "rc"),
    (date(2025, 12, 22), date(2025, 12, 30), "rc"),
    (date(2026, 3, 2), date(2026, 3, 14), "rc"),
    (date(2026, 4, 13), date(2026, 4, 25), "rc"),
    (date(2026, 5, 25), date(2026, 6, 6), "rc"),
    (date(2025, 11, 3), date(2025, 11, 4), "holiday"),
    (date(2025, 12, 31), date(2026, 1, 11), "holiday"),
    (date(2026, 2, 23), date(2026, 2, 23), "holiday"),
    (date(2026, 3, 9), date(2026, 3, 9), "holiday"),
    (date(2026, 5, 1), date(2026, 5, 2), "holiday"),
    (date(2026, 5, 11), date(2026, 5, 11), "holiday"),
    (date(2026, 6, 12), date(2026, 6, 13), "holiday"),
    (date(2026, 1, 12), date(2026, 1, 24), "exam"),
    (date(2026, 6, 9), date(2026, 6, 30), "exam"),
    (date(2026, 1, 26), date(2026, 1, 31), "vacation"),
]

STATUS_RU = {
    "odd": "неделя числитель (нечётная)",
    "even": "неделя знаменатель (чётная)",
    "holiday": "праздничный день",
    "vacation": "каникулы",
    "exam": "сессия",
    "rc": "рейтинговый контроль",
    "before": "до начала семестра",
}

def parity_ru_full(p: str) -> str:
    return STATUS_RU.get(p, p)

def get_week_status(d: date) -> str:
    if isinstance(d, datetime):
        d = d.date()
    for start, end, kind in SPECIAL_PERIODS:
        if start <= d <= end:
            return kind
    if d.isoweekday() == 7:
        return "holiday"
    week_num = ((d - SEMESTER_START).days // 7) + 1
    if week_num <= 0:
        return "before"
    block = (week_num - 1) // 7
    return "odd" if block % 2 == 0 else "even"

def norm(s: str) -> str:
    return (s or "").strip().lower()

def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)

# -------- storage --------
@dataclass
class Profile:
    institute_id: str | None = None
    institute_name: str | None = None
    course_num: int | None = None
    group_id: str | None = None
    group_name: str | None = None

PROFILES: dict[int, Profile] = {}
LISTS_CACHE: dict[int, dict] = {}

# -------- FSM --------
class Pick(StatesGroup):
    institute = State()
    course = State()
    group = State()

# -------- DB helpers --------
async def q_institutes(db):
    sql = "SELECT id, name FROM institutes ORDER BY name COLLATE NOCASE"
    async with db.execute(sql) as cur:
        return await cur.fetchall()

def _course_to_int(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None

async def q_courses_for_institute(db, institute_id: str) -> list[int]:
    sql = "SELECT DISTINCT COALESCE(course,'') FROM groups WHERE institute_id = ?"
    async with db.execute(sql, (institute_id,)) as cur:
        vals = [row[0] for row in await cur.fetchall()]
    ints = sorted(set([c for c in (_course_to_int(v) for v in vals) if c]))
    return ints

async def q_groups_by_institute_course(db, institute_id: str, course_num: int):
    sql = """
        SELECT id, name, COALESCE(course,'')
        FROM groups
        WHERE institute_id = ?
          AND (
                CAST(replace(replace(course,' курс',''),' курс.','') AS INT) = ?
                OR COALESCE(course,'') LIKE ?
              )
        ORDER BY name COLLATE NOCASE
    """
    like = f"{course_num}%"
    async with db.execute(sql, (institute_id, course_num, like)) as cur:
        return await cur.fetchall()

async def q_groups_by_institute(db, institute_id: str):
    sql = """
        SELECT id, name, COALESCE(course,'')
        FROM groups
        WHERE institute_id = ?
        ORDER BY name COLLATE NOCASE
    """
    async with db.execute(sql, (institute_id,)) as cur:
        return await cur.fetchall()

async def q_find_groups_by_name(db, name_like: str):
    q = norm(name_like).replace(" ", "")
    sql = """
      SELECT id, name, COALESCE(course,'')
      FROM groups
      WHERE REPLACE(LOWER(name),' ','') LIKE ?
      ORDER BY name COLLATE NOCASE
      LIMIT 30
    """
    async with db.execute(sql, (f"%{q}%",)) as cur:
        return await cur.fetchall()

async def q_lessons_for_day(db, group_id: str, day: int, parity: str | None):
    if parity in ("odd", "even"):
        sql = """
          SELECT day, time_start, time_end, title, teacher, room,
                 COALESCE(kind,'') as kind, week
          FROM lessons
          WHERE group_id = ?
            AND day = ?
            AND (week = 'all' OR week = ?)
          ORDER BY time_start
        """
        params = (group_id, day, parity)
    else:
        sql = """
          SELECT day, time_start, time_end, title, teacher, room,
                 COALESCE(kind,'') as kind, week
          FROM lessons
          WHERE group_id = ?
            AND day = ?
          ORDER BY
            CASE week WHEN 'all' THEN 0 WHEN 'odd' THEN 1 ELSE 2 END,
            time_start
        """
        params = (group_id, day)
    async with db.execute(sql, params) as cur:
        return await cur.fetchall()

async def q_lessons_for_week(db, group_id: str, parity: str):
    out = {}
    for d in range(1, 8):
        out[d] = await q_lessons_for_day(db, group_id, d, parity)
    return out

# -------- Formatting --------
def hesc(s: str | None) -> str:
    s = s or ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def html_quote(lines: list[str]) -> str:
    return "<blockquote>" + "\n".join(hesc(x) for x in lines) + "</blockquote>"

def row_to_lines(row) -> list[str]:
    _day, ts, te, title, teacher, room, kind, _week = row
    title_line = hesc(title)
    info_parts = []
    if ts and te:
        info_parts.append(f"{hesc(ts)}–{hesc(te)}")
    if kind:
        info_parts.append(hesc(kind.upper()))
    if room:
        info_parts.append(hesc(room))
    lines = [title_line]
    if teacher:
        lines.append(hesc(str(teacher)))
    lines.append("   ".join(info_parts))
    return lines

def render_day_block(header_line: str, rows) -> str:
    lines = [header_line]
    if not rows:
        lines.append("пар нет")
        return html_quote(lines)
    for r in rows:
        lines.extend(row_to_lines(r))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return html_quote(lines)

# -------- Keyboards --------
def kb_week_nav(week_start: date):
    """
    Клавиатура:
    [◀️] [🏠] [▶️]
    [Сменить группу]
    В колбэках шлём точные даты понедельников.
    """
    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)
    home_week = monday_of_week(tznow().date())

    kb = InlineKeyboardBuilder()
    kb.button(text="◀️", callback_data=f"week:{prev_week.isoformat()}")
    kb.button(text="🏠", callback_data=f"week:{home_week.isoformat()}")
    kb.button(text="▶️", callback_data=f"week:{next_week.isoformat()}")
    kb.button(text="Сменить группу", callback_data="menu:change")
    kb.adjust(3, 1)
    return kb.as_markup()

# -------- bot --------
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    PROFILES[m.chat.id] = PROFILES.get(m.chat.id, Profile())
    await m.answer(
        "Привет! Я бот расписания ВлГУ.\nВыберите институт:",
        reply_markup=await build_inst_page(0),
        parse_mode="HTML",
    )
    await state.set_state(Pick.institute)

async def build_inst_page(page: int):
    async with aiosqlite.connect(DB_PATH) as db:
        insts = await q_institutes(db)
    # пагинацию по институтам оставляем как было
    start = page*10
    chunk = insts[start:start+10]
    kb = InlineKeyboardBuilder()
    for inst_id, name in chunk:
        kb.button(text=name, callback_data=f"inst:{inst_id}")
    if page > 0:
        kb.button(text="« Назад", callback_data=f"instpage:{page-1}")
    if start+10 < len(insts):
        kb.button(text="Вперёд »", callback_data=f"instpage:{page+1}")
    kb.adjust(1)
    return kb.as_markup()

@dp.callback_query(F.data.startswith("inst:"))
async def pick_inst(c: CallbackQuery, state: FSMContext):
    inst_id = c.data.split(":")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        insts = await q_institutes(db)
        inst_name = next((n for i, n in insts if i == inst_id), inst_id)
        courses = await q_courses_for_institute(db, inst_id)
        groups_all = await q_groups_by_institute(db, inst_id)

    prof = PROFILES.get(c.message.chat.id) or Profile()
    prof.institute_id = inst_id
    prof.institute_name = inst_name
    prof.course_num = None
    prof.group_id = None
    prof.group_name = None
    PROFILES[c.message.chat.id] = prof

    LISTS_CACHE[c.message.chat.id] = {"groups_all": groups_all}

    await c.message.edit_text(
        f"<b>{hesc(inst_name)}</b>\nВыберите курс:",
        reply_markup=kb_courses(courses),
        parse_mode="HTML",
    )
    await state.set_state(Pick.course)
    await c.answer()

def kb_courses(course_nums: list[int]):
    kb = InlineKeyboardBuilder()
    for cnum in course_nums:
        kb.button(text=f"{cnum} курс", callback_data=f"course:{cnum}")
    kb.button(text="Все курсы", callback_data="course:all")
    kb.adjust(3)
    return kb.as_markup()

@dp.callback_query(F.data.startswith("course:"))
async def pick_course(c: CallbackQuery, state: FSMContext):
    sel = c.data.split(":")[1]
    prof = PROFILES.get(c.message.chat.id) or Profile()
    if not prof.institute_id:
        await c.answer("Сначала выберите институт", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        if sel == "all":
            groups = await q_groups_by_institute(db, prof.institute_id)
            prof.course_num = None
        else:
            course_num = int(sel)
            groups = await q_groups_by_institute_course(db, prof.institute_id, course_num)
            prof.course_num = course_num

    PROFILES[c.message.chat.id] = prof
    LISTS_CACHE[c.message.chat.id]["groups"] = groups

    title = f"{hesc(prof.institute_name)}\nКурс: {hesc(str(prof.course_num) if prof.course_num else 'все')}\nТеперь выберите группу или просто напишите её название:"
    await c.message.edit_text(
        title,
        reply_markup=kb_groups(groups, page=0),
        parse_mode="HTML",
    )
    await state.set_state(Pick.group)
    await c.answer()

def kb_groups(items, page=0, per_page=12):
    start = page*per_page
    chunk = items[start:start+per_page]
    kb = InlineKeyboardBuilder()
    for gid, name, _course in chunk:
        kb.button(text=name, callback_data=f"group:{gid}")
    if page > 0:
        kb.button(text="« Назад", callback_data=f"grouppage:{page-1}")
    if start+per_page < len(items):
        kb.button(text="Вперёд »", callback_data=f"grouppage:{page+1}")
    kb.adjust(3)
    return kb.as_markup()

@dp.callback_query(F.data.startswith("grouppage:"))
async def group_page(c: CallbackQuery, state: FSMContext):
    page = int(c.data.split(":")[1])
    groups = LISTS_CACHE.get(c.message.chat.id, {}).get("groups", [])
    await c.message.edit_text("Выберите группу:", reply_markup=kb_groups(groups, page=page), parse_mode="HTML")
    await state.set_state(Pick.group)
    await c.answer()

@dp.callback_query(F.data.startswith("group:"))
async def pick_group(c: CallbackQuery, state: FSMContext):
    gid = c.data.split(":")[1]
    groups = LISTS_CACHE.get(c.message.chat.id, {}).get("groups", []) or \
             LISTS_CACHE.get(c.message.chat.id, {}).get("groups_all", [])
    gname = next((n for i, n, _ in groups if i == gid), gid)

    prof = PROFILES.get(c.message.chat.id) or Profile()
    prof.group_id = gid
    prof.group_name = gname
    PROFILES[c.message.chat.id] = prof

    # Показ сразу недельного расписания (текущая неделя)
    current_monday = monday_of_week(tznow().date())
    await state.clear()
    await show_week_common(c, current_monday, replace_message=True)

# ---- free-text search: пользователь может просто набрать "КП-125"
@dp.message(F.text & ~F.text.startswith("/"))
async def free_text_pick_group(m: Message, state: FSMContext):
    query = m.text.strip()
    if len(query) < 2:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        found = await q_find_groups_by_name(db, query)
    if not found:
        await m.reply("Не нашёл такую группу. Попробуй ещё раз (минимум 2 символа).")
        return
    if len(found) == 1:
        gid, gname, _ = found[0]
        LISTS_CACHE[m.chat.id] = {"groups_all": found}
        fake_cb = CallbackQuery(id="0", from_user=m.from_user, chat_instance="", message=m, data=f"group:{gid}")
        await pick_group(fake_cb, state)
        return
    kb = InlineKeyboardBuilder()
    for gid, name, _ in found:
        kb.button(text=name, callback_data=f"group:{gid}")
    kb.adjust(2)
    await m.reply("Нашёл несколько групп. Выбери нужную:", reply_markup=kb.as_markup())

# ----- недельная навигация -----
@dp.callback_query(F.data.startswith("week:"))
async def navigate_week(c: CallbackQuery):
    # формат колбэка: week:YYYY-MM-DD
    iso = c.data.split(":")[1]
    try:
        week_start = date.fromisoformat(iso)
    except ValueError:
        week_start = monday_of_week(tznow().date())
    await show_week_common(c, week_start)

async def show_week_common(c: CallbackQuery, week_start: date, replace_message: bool = False):
    prof = PROFILES.get(c.message.chat.id)
    if not prof or not prof.group_id:
        await c.answer("Сначала выберите группу", show_alert=True)
        return

    status = get_week_status(week_start)
    header = html_quote([f"{prof.group_name}", f"неделя: {parity_ru_full(status)}"])

    # Если неделя «не учебная» — сообщаем об этом
    if status not in ("odd", "even", "rc"):
        body = html_quote([
            f"{DAY_SHORT[1]}–{DAY_SHORT[7]} ~ {week_start.strftime('%d.%m')}–{(week_start+timedelta(days=6)).strftime('%d.%m')}",
            "На этой неделе занятий нет"
        ])
        text = f"{header}\n\n{body}"
        try:
            await c.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=kb_week_nav(week_start)
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
        await c.answer()
        return

    # Для 'rc' используем те же данные, что и для «even»
    parity = "odd" if status == "odd" else "even"

    async with aiosqlite.connect(DB_PATH) as db:
        by_day = await q_lessons_for_week(db, prof.group_id, parity=parity)

    blocks = [header]
    for d in range(1, 8):
        ddate = week_start + timedelta(days=d - 1)
        label = f"{DAY_SHORT[d]} ~ {ddate.strftime('%d.%m')}"
        blocks.append(render_day_block(label, by_day.get(d, [])))

    text = "\n".join(blocks)
    try:
        await c.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=kb_week_nav(week_start)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await c.answer()

# -------- Смена группы --------
@dp.callback_query(F.data == "menu:change")
async def change_group(c: CallbackQuery, state: FSMContext):
    await on_start(c.message, state)
    await c.answer()

# -------- run --------
async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")
    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
