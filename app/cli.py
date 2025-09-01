from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional, Literal

import typer

# Локальные импорты
from . import vlsu_api
from .vlsu_api import (
    find_institute_id,
    get_groups,
    find_group,
    get_schedule,
    get_group_current_info,
)

app = typer.Typer(help="VLSU schedule CLI")

# ----------------- Утилиты -----------------

DAYS_RU = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
    7: "Воскресенье",
}

def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().lower()

def _is_uuid(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", _norm(s)))

def _column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table});")
    cols = [row[1] for row in cur.fetchall()]
    return column in cols

def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

# ---------- преобразование к «красивому» JSON ----------

def _structured_payload(
    *,
    institute_id: str,
    institute_name: str,
    form: int,
    group: dict,
    lessons: list[dict],
) -> dict:
    """
    Собираем иерархию:
    {
      "meta": {...},
      "weeks": {
        "all": { "1": [...], "2": [...], ... },
        "odd": { ... },
        "even": { ... }
      }
    }
    где ключи "1".."7" — дни недели, плюс дублируем "name": "Понедельник".
    """
    weeks: dict[str, dict[str, list[dict]]] = {
        "all": {str(i): [] for i in range(1, 8)},
        "odd": {str(i): [] for i in range(1, 8)},
        "even": {str(i): [] for i in range(1, 8)},
    }

    def pack(lesson: dict) -> dict:
        return {
            "time": {"start": lesson.get("start"), "end": lesson.get("end")},
            "title": lesson.get("title"),
            "teacher": lesson.get("teacher"),
            "room": lesson.get("room"),
            "kind": lesson.get("kind"),
        }

    for l in lessons or []:
        wk = (l.get("week") or "all").lower()
        if wk not in weeks:
            wk = "all"
        day = int(l.get("day") or 0)
        if day < 1 or day > 7:
            continue
        weeks[wk][str(day)].append(pack(l))

    # Уберём пустые дни из каждого блока недели и добавим «name»
    for wk in ("all", "odd", "even"):
        cleaned: dict[str, dict] = {}
        for day_key, arr in weeks[wk].items():
            if not arr:
                continue
            cleaned[day_key] = {
                "name": DAYS_RU.get(int(day_key), day_key),
                "lessons": arr,
            }
        weeks[wk] = cleaned

    payload = {
        "meta": {
            "generated_at_utc": _utc_iso(),
            "institute": {"id": institute_id, "name": institute_name},
            "form": {0: "очная", 1: "заочная", 2: "очно-заочная"}.get(form, str(form)),
            "group": {"id": group["id"], "name": group["name"], "course": group.get("course")},
            "schema": {
                "weeks": ["all", "odd", "even"],
                "day_keys": "1..7 (1=Пн ... 7=Вс, но сохраняются только непустые дни)",
            },
        },
        "weeks": weeks,
    }
    return payload

# ----------------- Команды: справочники и просмотр -----------------

@app.command()
def institutes():
    """Показать все институты."""
    data = vlsu_api.get_institutes()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Всего институтов: {len(data)}")

@app.command()
def groups(
    institute: Annotated[str, typer.Option("--institute", "-i")],
    form: Annotated[int, typer.Option("--form", "-f")] = 0,
    debug: Annotated[bool, typer.Option("--debug")] = False,
):
    """
    Показать группы по институту.
    form: 0 — очная, 1 — заочная, 2 — очно-заочная.
    """
    inst_id = institute if _is_uuid(institute) else vlsu_api.find_institute_id(institute)
    if not inst_id:
        typer.secho(f"Институт не найден по: {institute}", fg="red")
        data = vlsu_api.get_institutes()
        hint = [x for x in data if _norm(institute) in _norm(x["Text"]) ]
        if hint:
            typer.echo("Возможно, вы имели в виду:")
            for h in hint:
                typer.echo(f"- {h['Text']} ({h['Value']})")
        raise typer.Exit(code=1)

    gs = vlsu_api.get_groups(inst_id, form=form, debug=debug)
    if not gs:
        typer.secho("Группы не найдены (проверь форму обучения).", fg="yellow")
    else:
        print(json.dumps(gs[:30], ensure_ascii=False, indent=2))
        print(f"Всего групп: {len(gs)}")

@app.command()
def schedule(
    institute: Annotated[str, typer.Option("--institute", "-i")],
    group: Annotated[str, typer.Option("--group", "-g")],
    form: Annotated[int, typer.Option("--form", "-f")] = 0,
    week: Annotated[int, typer.Option("--week", "-w")] = -1,
    now: Annotated[bool, typer.Option("--now")] = False,
    raw: Annotated[bool, typer.Option("--raw")] = False,
    debug: Annotated[bool, typer.Option("--debug")] = False,
):
    """
    Показать расписание группы.
    --now: дополнительно вывести «сейчас/следующая».
    --week: -1 все, 1 — числитель (odd), 2 — знаменатель (even).
    --raw: вывести как вернул API (без нормализации).
    """
    inst_id = institute if _is_uuid(institute) else vlsu_api.find_institute_id(institute)
    if not inst_id:
        typer.secho(f"Институт не найден по: {institute}", fg="red")
        raise typer.Exit(code=1)

    gs = vlsu_api.get_groups(inst_id, form=form, debug=debug)
    g = vlsu_api.find_group(gs, group) or vlsu_api.find_group(gs, group.replace(" ", ""))  # пробуем без пробелов
    if not g:
        typer.secho(f"Группа не найдена: {group}", fg="red")
        maybe = [x for x in gs if _norm(group) in _norm(x["name"])]
        if maybe[:10]:
            typer.echo("Похожие группы:")
            for x in maybe[:10]:
                typer.echo(f"- {x['name']} (course={x.get('course')})")
        raise typer.Exit(code=1)

    if now:
        cur = vlsu_api.get_group_current_info(g["id"], debug=debug)
        print("CURRENT:", json.dumps(cur, ensure_ascii=False, indent=2))

    week_type = None if week == -1 else week
    sch = vlsu_api.get_schedule(g["id"], week_type=week_type, raw=raw, debug=debug)
    print(json.dumps(sch, ensure_ascii=False, indent=2))
    print(f"OK: {g['name']} ({g['id']})")

# ----------------- SQLite: схема и сохранение -----------------

def _db_init(conn: sqlite3.Connection):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS institutes(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            course TEXT,
            institute_id TEXT
        );
    """)

    # ⬇️ добавили kind в CREATE TABLE
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lessons(
            group_id TEXT NOT NULL,
            day INTEGER NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            title TEXT,
            teacher TEXT,
            room TEXT,
            kind TEXT,
            week TEXT CHECK(week IN ('all','odd','even')) NOT NULL DEFAULT 'all'
        );
    """)

    # «мягкие» миграции (если таблица была создана раньше без нужных полей)
    for col, ddl in [
        ("day",        "ALTER TABLE lessons ADD COLUMN day INTEGER NOT NULL DEFAULT 0;"),
        ("time_start", "ALTER TABLE lessons ADD COLUMN time_start TEXT NOT NULL DEFAULT '';"),
        ("time_end",   "ALTER TABLE lessons ADD COLUMN time_end TEXT NOT NULL DEFAULT '';"),
        ("title",      "ALTER TABLE lessons ADD COLUMN title TEXT;"),
        ("teacher",    "ALTER TABLE lessons ADD COLUMN teacher TEXT;"),
        ("room",       "ALTER TABLE lessons ADD COLUMN room TEXT;"),
        ("kind",       "ALTER TABLE lessons ADD COLUMN kind TEXT;"),   # ⬅️ добавили
        ("week",       "ALTER TABLE lessons ADD COLUMN week TEXT DEFAULT 'all';"),
    ]:
        if not _column_exists(cur, "lessons", col):
            cur.execute(ddl)

    conn.commit()


def _db_save_group(conn: sqlite3.Connection, g: dict, institute_id: str, institute_name: Optional[str] = None):
    cur = conn.cursor()
    if institute_name:
        cur.execute("INSERT OR IGNORE INTO institutes(id, name) VALUES(?, ?);", (institute_id, institute_name))
    cur.execute(
        """
        INSERT OR REPLACE INTO groups(id, name, course, institute_id)
        VALUES(?,?,?,?);
        """,
        (g["id"], g["name"], g.get("course"), institute_id),
    )
    conn.commit()

def _db_save_lessons(conn: sqlite3.Connection, group_id: str, lessons: list[dict]):
    cur = conn.cursor()
    cur.execute("DELETE FROM lessons WHERE group_id = ?;", (group_id,))
    if lessons:
        cur.executemany(
            """
            INSERT INTO lessons(
                group_id, day, time_start, time_end, title, teacher, room, kind, week
            )
            VALUES(?,?,?,?,?,?,?,?,?);
            """,
            [
                (
                    group_id,
                    int(x.get("day", 0)),
                    x.get("start", ""),
                    x.get("end", ""),
                    x.get("title"),
                    x.get("teacher"),
                    x.get("room"),
                    x.get("kind"),
                    x.get("week", "all"),
                )
                for x in lessons
            ],
        )
    conn.commit()


# ----------------- Команда dump -----------------

@app.command(help="Сохранить расписание группы: по умолчанию — красивый JSON; опц. SQLite.")
def dump(
    institute: Annotated[str, typer.Option("-i", "--institute", help="Название или GUID института")],
    group: Annotated[str, typer.Option("-g", "--group", help="Название группы, напр. 'КП-125'")],
    form: Annotated[int, typer.Option("-f", "--form", help="0 очная, 1 заочная, 2 очно-заочная")] = 0,
    week: Annotated[int, typer.Option("-w", "--week", help="-1 все, 1 числитель, 2 знаменатель")] = -1,
    outdir: Annotated[Path, typer.Option("--outdir", help="Папка для сохранения JSON")]=Path("out"),
    db: Annotated[Optional[Path], typer.Option("--db", help="Путь к SQLite файлу (опционально)")]=None,
    flat: Annotated[bool, typer.Option("--flat", help="Сохранить старый плоский список (а не иерархию)")] = False,
    debug: Annotated[bool, typer.Option("--debug")] = False,
):
    # Институт
    inst_id = institute if _is_uuid(institute) else vlsu_api.find_institute_id(institute)
    if not inst_id:
        typer.secho(f"Институт не найден по: {institute}", fg="red")
        raise typer.Exit(1)

    # Группа
    gs = get_groups(inst_id, form=form, debug=debug)
    g = find_group(gs, group) or find_group(gs, group.replace(" ", ""))
    if not g:
        typer.secho(f"Группа не найдена: {group}", fg="red")
        raise typer.Exit(1)

    # Расписание (нормализованное)
    week_type = None if week == -1 else week
    lessons = vlsu_api.get_schedule(g["id"], week_type=week_type, raw=False, debug=debug)

    # JSON
    outdir.mkdir(parents=True, exist_ok=True)
    out_path = outdir / f"{g['name']}.json"

    if flat:
        out_path.write_text(json.dumps(lessons, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        meta_name = institute if not _is_uuid(institute) else next(
            (x["Text"] for x in vlsu_api.get_institutes() if x.get("Value") == inst_id), inst_id
        )
        structured = _structured_payload(
            institute_id=inst_id,
            institute_name=str(meta_name),
            form=form,
            group=g,
            lessons=lessons if isinstance(lessons, list) else [],
        )
        out_path.write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8")

    # SQLite (опционально)
    if db:
        conn = sqlite3.connect(str(db))
        _db_init(conn)
        _db_save_group(conn, g, inst_id, institute_name=None if _is_uuid(institute) else institute)
        _db_save_lessons(conn, g["id"], lessons if isinstance(lessons, list) else [])
        conn.close()

    # Сводка
    if isinstance(lessons, list):
        total = len(lessons)
        reg = sum(1 for x in lessons if x.get("week") == "all")
        odd = sum(1 for x in lessons if x.get("week") == "odd")
        even = sum(1 for x in lessons if x.get("week") == "even")
        typer.echo(
            f"Сохранено: {out_path} — всего пар: {total} "
            f"(регулярных: {reg}, нечётн.: {odd}, чётн.: {even})"
        )
    else:
        typer.echo(f"Сохранено: {out_path} — (сырые данные)")


@app.command(help="Спарсить ВСЕ группы института по всем формам обучения и сохранить в JSON/SQLite.")
def dump_all(
    institute: Annotated[str, typer.Option("-i", "--institute", help="Название или GUID института")],
    outdir: Annotated[Path, typer.Option("--outdir", help="Папка для JSON")]=Path("out"),
    db: Annotated[Optional[Path], typer.Option("--db", help="Путь к SQLite (опционально)")]=None,
    week: Annotated[int, typer.Option("-w", "--week", help="-1 все, 1 числитель, 2 знаменатель")] = -1,
    forms: Annotated[str, typer.Option("--forms", help="Список форм через запятую: 0 (очная),1 (заочная),2 (очно-заочная)")] = "0,1,2",
    delay: Annotated[float, typer.Option("--delay", help="Пауза между запросами, сек")] = 0.4,
    debug: Annotated[bool, typer.Option("--debug")] = False,
):
    import time
    inst_id = institute if _is_uuid(institute) else find_institute_id(institute)
    if not inst_id:
        typer.secho(f"Институт не найден по: {institute}", fg="red")
        raise typer.Exit(1)

    # Человеческое имя института
    inst_name = institute if _is_uuid(institute) else institute
    if _is_uuid(institute):
        inst_name = next((x["Text"] for x in vlsu_api.get_institutes() if x.get("Value")==inst_id), inst_id)

    if db:
        conn = sqlite3.connect(str(db))
        _db_init(conn)
    else:
        conn = None

    forms_list = [int(x) for x in re.split(r"[,\s]+", forms.strip()) if x != ""]

    total_groups = 0
    total_lessons = 0
    week_type = None if week == -1 else week

    for form in forms_list:
        try:
            groups = get_groups(inst_id, form=form, debug=debug) or []
        except Exception as e:
            typer.secho(f"[{form}] Ошибка получения групп: {e}", fg="red")
            continue

        if not groups:
            typer.secho(f"[{form}] Групп не найдено.", fg="yellow")
            continue

        typer.secho(f"[{form}] Групп: {len(groups)}", fg="cyan")
        for g in groups:
            total_groups += 1
            try:
                lessons = get_schedule(g["id"], week_type=week_type, raw=False, debug=debug)
            except Exception as e:
                typer.secho(f"  {g['name']}: ошибка расписания: {e}", fg="red")
                continue

            # JSON: out/<form>/<group>.json
            form_dir = outdir / {0:"очная",1:"заочная",2:"очно-заочная"}.get(form, str(form))
            form_dir.mkdir(parents=True, exist_ok=True)

            payload = _structured_payload(
                institute_id=inst_id,
                institute_name=str(inst_name),
                form=form,
                group=g,
                lessons=lessons if isinstance(lessons, list) else [],
            )
            (form_dir / f"{g['name']}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # SQLite (если указан)
            if conn:
                try:
                    _db_save_group(conn, g, inst_id, institute_name=str(inst_name))
                    _db_save_lessons(conn, g["id"], lessons if isinstance(lessons, list) else [])
                except Exception as e:
                    typer.secho(f"  {g['name']}: ошибка записи в БД: {e}", fg="red")

            if isinstance(lessons, list):
                total_lessons += len(lessons)

            typer.echo(f"  ✓ {g['name']} — {len(lessons) if isinstance(lessons,list) else 0} пар")
            time.sleep(delay)

    if conn:
        conn.close()

    typer.secho(f"ГОТОВО: групп обработано {total_groups}, пар сохранено ~{total_lessons}", fg="green")


# --- общий хелпер: выгрузка одного института (используется dump-all и dump-universe)
def _dump_institute(inst_id: str, inst_name: str, *, outdir: Path, db_path: Optional[Path],
                    week: int, forms: list[int], delay: float, debug: bool) -> tuple[int,int]:
    import time, sqlite3, json, re
    week_type = None if week == -1 else week

    # БД (одна на все институты, если указана)
    conn = sqlite3.connect(str(db_path)) if db_path else None
    if conn: _db_init(conn)

    total_groups = 0
    total_lessons = 0

    for form in forms:
        # папка вида: out/<Институт>/<очная|заочная|очно-заочная>
        form_dir = outdir / inst_name / {0:"очная",1:"заочная",2:"очно-заочная"}.get(form, str(form))
        form_dir.mkdir(parents=True, exist_ok=True)

        try:
            groups = get_groups(inst_id, form=form, debug=debug) or []
        except Exception as e:
            typer.secho(f"[{inst_name} / {form}] Ошибка groups: {e}", fg="red")
            continue

        typer.secho(f"[{inst_name}] форма {form}: групп {len(groups)}", fg="cyan")

        for g in groups:
            total_groups += 1
            try:
                lessons = get_schedule(g["id"], week_type=week_type, raw=False, debug=debug)
            except Exception as e:
                typer.secho(f"  {g['name']}: ошибка расписания: {e}", fg="red")
                continue

            # красивый JSON
            payload = _structured_payload(
                institute_id=inst_id,
                institute_name=str(inst_name),
                form=form,
                group=g,
                lessons=lessons if isinstance(lessons, list) else [],
            )
            (form_dir / f"{g['name']}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # SQLite
            if conn:
                try:
                    _db_save_group(conn, g, inst_id, institute_name=str(inst_name))
                    _db_save_lessons(conn, g["id"], lessons if isinstance(lessons, list) else [])
                except Exception as e:
                    typer.secho(f"  {g['name']}: ошибка записи в БД: {e}", fg="red")

            if isinstance(lessons, list):
                total_lessons += len(lessons)

            typer.echo(f"  ✓ {g['name']} — {len(lessons) if isinstance(lessons,list) else 0} пар")
            time.sleep(delay)

    if conn: conn.close()
    return total_groups, total_lessons


@app.command(help="Спарсить ВСЕ институты (все формы, все группы). Можно фильтровать по имени.")
def dump_universe(
    outdir: Annotated[Path, typer.Option("--outdir", help="Корневая папка JSON")]=Path("out_all"),
    db: Annotated[Optional[Path], typer.Option("--db", help="Одна общая SQLite-БД (опционально)")]=None,
    week: Annotated[int, typer.Option("-w", "--week", help="-1 все, 1 числитель, 2 знаменатель")] = -1,
    forms: Annotated[str, typer.Option("--forms", help="Формы через запятую, по умолчанию 0,1,2")] = "0,1,2",
    name_like: Annotated[Optional[str], typer.Option("--name-like", help="Фильтр по названию института (подстрока, регвыр)")] = None,
    delay: Annotated[float, typer.Option("--delay", help="Пауза между запросами, сек")] = 0.4,
    debug: Annotated[bool, typer.Option("--debug")] = False,
):
    import re
    insts = vlsu_api.get_institutes()
    if name_like:
        rx = re.compile(name_like, re.I)
        insts = [x for x in insts if rx.search(str(x.get("Text","")))]
        if not insts:
            typer.secho("Ничего не совпало по --name-like", fg="yellow")
            raise typer.Exit(1)

    forms_list = [int(x) for x in re.split(r"[,\s]+", forms.strip()) if x!=""]

    grand_groups = 0
    grand_lessons = 0

    for it in insts:
        inst_id = it.get("Value")
        inst_name = it.get("Text") or inst_id
        g, l = _dump_institute(inst_id, inst_name,
                               outdir=outdir, db_path=db,
                               week=week, forms=forms_list,
                               delay=delay, debug=debug)
        grand_groups += g
        grand_lessons += l

    typer.secho(f"ГОТОВО: институтов {len(insts)}, групп {grand_groups}, пар ~{grand_lessons}", fg="green")


# ----------------- Точка входа -----------------

if __name__ == "__main__":
    app()
