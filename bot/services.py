from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Attendance, Municipality, Role, Task, TaskMunicipality, TaskStatus, User


async def get_or_create_municipality(session: AsyncSession, name: str) -> Municipality:
    result = await session.execute(select(Municipality).where(Municipality.name == name))
    municipality = result.scalars().first()
    if municipality:
        return municipality

    municipality = Municipality(name=name)
    session.add(municipality)
    await session.commit()
    await session.refresh(municipality)
    return municipality


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    full_name: str,
    role: Role,
    municipality: Optional[Municipality] = None,
    primary_unit: Optional[str] = None,
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalars().first()
    if user:
        return user

    user = User(
        telegram_id=telegram_id,
        full_name=full_name,
        role=role,
        municipality=municipality,
        primary_unit=primary_unit,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def list_tasks_for_period(
    session: AsyncSession,
    municipality_ids: Optional[Iterable[int]] = None,
    upcoming_only: bool = False,
    completed_only: bool = False,
    days: int = 7,
) -> List[Task]:
    query = select(Task).order_by(Task.starts_at.asc())

    if upcoming_only:
        query = query.where(Task.status == TaskStatus.UPCOMING)
    elif completed_only:
        query = query.where(Task.status == TaskStatus.COMPLETED)

    if days:
        end_date = datetime.utcnow() + timedelta(days=days)
        query = query.where(Task.starts_at <= end_date)

    if municipality_ids:
        query = query.join(Task.municipalities).where(TaskMunicipality.municipality_id.in_(municipality_ids))

    result = await session.execute(query)
    return list(result.scalars().unique())


async def create_task(
    session: AsyncSession,
    title: str,
    description: str,
    starts_at: datetime,
    location: str,
    municipalities: Iterable[Municipality],
) -> Task:
    task = Task(title=title, description=description, starts_at=starts_at, location=location)
    session.add(task)
    await session.flush()

    for municipality in municipalities:
        link = TaskMunicipality(task=task, municipality=municipality)
        session.add(link)

    await session.commit()
    await session.refresh(task)
    return task


async def set_task_status(session: AsyncSession, task: Task, status: TaskStatus) -> Task:
    task.status = status
    await session.commit()
    await session.refresh(task)
    return task


async def toggle_attendance(session: AsyncSession, task: Task, user: User, attended: bool) -> Attendance:
    result = await session.execute(
        select(Attendance).where(Attendance.task_id == task.id, Attendance.user_id == user.id)
    )
    record = result.scalars().first()

    if not record:
        record = Attendance(task=task, user=user)
        session.add(record)

    record.attended = attended
    record.marked_at = datetime.utcnow()
    await session.commit()
    await session.refresh(record)
    return record


async def task_stats(session: AsyncSession, task: Task) -> List[Tuple[str, int, int]]:
    query = (
        select(
            Municipality.name,
            func.sum(case((Attendance.attended.is_(True), 1), else_=0)),
            func.count(User.id),
        )
        .select_from(TaskMunicipality)
        .join(Municipality, TaskMunicipality.municipality_id == Municipality.id)
        .join(User, User.municipality_id == Municipality.id)
        .outerjoin(Attendance, (Attendance.user_id == User.id) & (Attendance.task_id == task.id))
        .where(TaskMunicipality.task_id == task.id)
        .group_by(Municipality.name)
        .order_by(Municipality.name)
    )

    result = await session.execute(query)
    stats: List[Tuple[str, int, int]] = []
    for name, attended_count, total in result:
        stats.append((name, attended_count or 0, total or 0))
    return stats


async def task_attendance_details(session: AsyncSession, task: Task, municipality: Municipality) -> List[Tuple[str, bool]]:
    query = (
        select(User.full_name, Attendance.attended)
        .select_from(User)
        .outerjoin(Attendance, (Attendance.user_id == User.id) & (Attendance.task_id == task.id))
        .where(User.municipality_id == municipality.id)
        .order_by(User.full_name)
    )
    result = await session.execute(query)
    return [(name, attended or False) for name, attended in result]


async def task_is_available_for_municipality(
    session: AsyncSession, task_id: int, municipality_id: int
) -> bool:
    query = select(TaskMunicipality.id).where(
        TaskMunicipality.task_id == task_id,
        TaskMunicipality.municipality_id == municipality_id,
    )
    result = await session.execute(query)
    return result.scalar_one_or_none() is not None


async def list_primary_units(session: AsyncSession, municipality_id: int) -> List[str]:
    query = (
        select(User.primary_unit)
        .where(User.municipality_id == municipality_id, User.primary_unit.is_not(None))
        .distinct()
        .order_by(User.primary_unit)
    )
    result = await session.execute(query)
    return [value for value, in result if value]


async def list_users_in_primary_unit(
    session: AsyncSession, municipality_id: int, primary_unit: str
) -> List[User]:
    query = (
        select(User)
        .where(
            User.municipality_id == municipality_id,
            User.primary_unit == primary_unit,
        )
        .order_by(User.full_name)
    )
    result = await session.execute(query)
    return list(result.scalars().unique())
