from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Iterable

class Database:
    def __init__(self, path: Path | str = "vlsu.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self):
        cur = self.conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS institutes(
            id   TEXT PRIMARY KEY,
            name TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS groups(
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            institute_id TEXT NOT NULL,
            form         INTEGER NOT NULL,     -- 0 очная, 1 заочная, 2 очно-заочная
            course       TEXT,
            FOREIGN KEY(institute_id) REFERENCES institutes(id) ON DELETE CASCADE
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS lessons(
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            day      INTEGER,                  -- 1..6 (если известно)
            start    TEXT,
            end      TEXT,
            title    TEXT,
            teacher  TEXT,
            room     TEXT,
            kind     TEXT,
            week     TEXT CHECK(week IN ('all','odd','even')),
            FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lessons_group ON lessons(group_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lessons_week  ON lessons(week)")
        self.conn.commit()

    def upsert_institute(self, inst_id: str, name: str):
        self.conn.execute("""
        INSERT INTO institutes(id, name) VALUES(?, ?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name
        """, (inst_id, name))
        self.conn.commit()

    def upsert_group(self, gid: str, name: str, inst_id: str, form: int, course: str | None):
        self.conn.execute("""
        INSERT INTO groups(id, name, institute_id, form, course)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            institute_id=excluded.institute_id,
            form=excluded.form,
            course=excluded.course
        """, (gid, name, inst_id, form, course))
        self.conn.commit()

    def replace_lessons(self, group_id: str, lessons: Iterable[dict]):
        self.conn.execute("DELETE FROM lessons WHERE group_id=?", (group_id,))
        self.conn.executemany("""
        INSERT INTO lessons(group_id, day, start, end, title, teacher, room, kind, week)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (group_id,
             l.get("day"), l.get("start"), l.get("end"),
             l.get("title"), l.get("teacher"), l.get("room"),
             l.get("kind"), l.get("week"))
            for l in lessons
        ])
        self.conn.commit()

    def close(self):
        self.conn.close()
