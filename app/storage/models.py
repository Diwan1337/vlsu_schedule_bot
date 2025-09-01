from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, ForeignKey, DateTime
from datetime import datetime

class Base(DeclarativeBase): pass

class Institute(Base):
    __tablename__ = "institutes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)

class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[int] = mapped_column(ForeignKey("institutes.id"))
    code: Mapped[str] = mapped_column(String(50), index=True)  # КП-125
    course: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))

class ScheduleItem(Base):
    __tablename__ = "schedule_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 1..6
    pair_no: Mapped[int] = mapped_column(Integer)  # 1..7
    start: Mapped[str] = mapped_column(String(5))  # "08:30"
    end:   Mapped[str] = mapped_column(String(5))  # "10:00"
    subject: Mapped[str] = mapped_column(String(300))
    kind:    Mapped[str] = mapped_column(String(50))   # лек/пр/лаб
    room:    Mapped[str] = mapped_column(String(50))
    teacher: Mapped[str] = mapped_column(String(200))
    week_type: Mapped[str] = mapped_column(String(20)) # all/числитель/знаменатель
    source_url: Mapped[str] = mapped_column(String(500))
    src_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
