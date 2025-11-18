import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Role(str, enum.Enum):
    ADMIN = "admin"
    MUNICIPALITY = "municipality"


class TaskStatus(str, enum.Enum):
    UPCOMING = "upcoming"
    COMPLETED = "completed"


class Municipality(Base):
    __tablename__ = "municipalities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)

    users: Mapped[List["User"]] = relationship("User", back_populates="municipality")
    tasks: Mapped[List["TaskMunicipality"]] = relationship(
        "TaskMunicipality", back_populates="municipality", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("telegram_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    municipality_id: Mapped[Optional[int]] = mapped_column(ForeignKey("municipalities.id"))
    primary_unit: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    municipality: Mapped[Optional[Municipality]] = relationship("Municipality", back_populates="users")
    attendance: Mapped[List["Attendance"]] = relationship("Attendance", back_populates="user")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.UPCOMING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    municipalities: Mapped[List["TaskMunicipality"]] = relationship(
        "TaskMunicipality", back_populates="task", cascade="all, delete-orphan"
    )
    attendance: Mapped[List["Attendance"]] = relationship("Attendance", back_populates="task")


class TaskMunicipality(Base):
    __tablename__ = "task_municipality"
    __table_args__ = (UniqueConstraint("task_id", "municipality_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"), nullable=False)

    task: Mapped[Task] = relationship("Task", back_populates="municipalities")
    municipality: Mapped[Municipality] = relationship("Municipality", back_populates="tasks")


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("task_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    attended: Mapped[bool] = mapped_column(Boolean, default=False)
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped[Task] = relationship("Task", back_populates="attendance")
    user: Mapped[User] = relationship("User", back_populates="attendance")
