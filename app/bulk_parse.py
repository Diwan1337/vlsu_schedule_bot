from __future__ import annotations
import argparse
import sqlite3
from typing import Optional
from pathlib import Path
from time import sleep

# наш код
from app import vlsu_api

# ---------- DB helpers (тот же формат, что в app/cli.py) ----------
def _column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table});")
    cols = [row[1] for row in cur.fetchall()]
    return column in cols

def db_init(conn: sqlite3.Connection):
    cur = conn.cursor()
    # institutes
    cur.execute("""
        CREATE TABLE IF NOT EXISTS institutes(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
    """)
    # groups
    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            course TEXT,
            institute_id TEXT
        );
    """)
    # lessons
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lessons(
            group_id TEXT NOT NULL,
            day INTEGER NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            title TEXT,
            teacher TEXT,
            room TEXT,
            week TEXT CHECK(week IN ('all','odd','even')) NOT NULL DEFAULT 'all',
            kind TEXT
        );
    """)

    # мягкие миграции — если база старая
    for col, ddl in [
        ("day",        "ALTER TABLE lessons ADD COLUMN day INTEGER NOT NULL DEFAULT 0;"),
        ("time_start", "ALTER TABLE lessons ADD COLUMN time_start TEXT NOT NULL DEFAULT '';"),
        ("time_end",   "ALTER TABLE lessons ADD COLUMN time_end TEXT NOT NULL DEFAULT '';"),
        ("title",      "ALTER TABLE lessons ADD COLUMN title TEXT;"),
        ("teacher",    "ALTER TABLE lessons ADD COLUMN teacher TEXT;"),
        ("room",       "ALTER TABLE lessons ADD COLUMN room TEXT;"),
        ("week",       "ALTER TABLE lessons ADD COLUMN week TEXT DEFAULT 'all';"),
        ("kind",       "ALTER TABLE lessons ADD COLUMN kind TEXT;"),
    ]:
        if not _column_exists(cur, "lessons", col):
            cur.execute(ddl)
    conn.commit()

def db_save_institute(conn: sqlite3.Connection, inst_id: str, name: str):
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO institutes(id, name) VALUES(?, ?);", (inst_id, name))
    conn.commit()

def db_save_group(conn: sqlite3.Connection, g: dict, institute_id: str):
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO groups(id, name, course, institute_id) VALUES(?,?,?,?);",
        (g["id"], g["name"], g.get("course"), institute_id),
    )
    conn.commit()

def db_save_lessons(conn: sqlite3.Connection, group_id: str, lessons: list[dict]):
    cur = conn.cursor()
    cur.execute("DELETE FROM lessons WHERE group_id = ?;", (group_id,))
    if lessons:
        cur.executemany(
            """
            INSERT INTO lessons(group_id, day, time_start, time_end, title, teacher, room, week, kind)
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
                    x.get("week", "all"),
                    x.get("kind"),
                )
                for x in lessons
            ],
        )
    conn.commit()

# ---------- Main harvesting ----------
def harvest_all(db_path: Path, forms: list[int], only_institute: Optional[str] = None, debug: bool = False, pause: float = 0.2):
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    db_init(conn)

    # список институтов
    institutes = vlsu_api.get_institutes()
    if only_institute:
        institutes = [x for x in institutes if x.get("Value") == only_institute]

    total_groups = 0
    total_lessons = 0

    for inst in institutes:
        inst_id = inst.get("Value")
        inst_name = inst.get("Text") or inst_id
        db_save_institute(conn, inst_id, inst_name)
        print(f"\n=== Институт: {inst_name} ({inst_id}) ===")

        for form in forms:
            print(f"  Форма {form} — загружаю группы...")
            try:
                groups = vlsu_api.get_groups(inst_id, form=form, debug=debug)
            except Exception as e:
                print(f"    [WARN] Не удалось получить группы (form={form}): {e}")
                continue

            print(f"    Групп: {len(groups)}")
            for g in groups:
                total_groups += 1
                db_save_group(conn, g, inst_id)
                gid, gname = g["id"], g["name"]

                try:
                    lessons = vlsu_api.get_schedule(gid, week_type=None, raw=False, debug=debug)
                except Exception as e:
                    print(f"    [FAIL] {gname}: {e}")
                    continue

                db_save_lessons(conn, gid, lessons if isinstance(lessons, list) else [])
                cnt = len(lessons) if isinstance(lessons, list) else 0
                total_lessons += cnt
                print(f"    [OK] {gname:20s} — {cnt:3d} пар")
                sleep(pause)  # чтобы не долбить API слишком часто

    conn.close()
    print(f"\nГотово. Всего групп обработано: {total_groups}, всего пар сохранено: {total_lessons}")
    print(f"База: {db_path}")

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Парсинг всех институтов/групп в SQLite")
    ap.add_argument("--db", required=True, help="Путь к SQLite файлу (.db/.sqlite)")
    ap.add_argument("--forms", nargs="+", type=int, default=[0], help="Список форм обучения (0 очная, 1 заочная, 2 очно-заочная)")
    ap.add_argument("--only-institute", help="Опционально: GUID института (Value) для выборочного обновления")
    ap.add_argument("--debug", action="store_true", help="Логировать сетевые запросы")
    ap.add_argument("--pause", type=float, default=0.2, help="Пауза между группами, сек")
    args = ap.parse_args()

    harvest_all(
        db_path=Path(args.db),
        forms=args.forms,
        only_institute=args.only_institute,
        debug=args.debug,
        pause=args.pause,
    )

if __name__ == "__main__":
    main()
