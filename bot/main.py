import asyncio
import io
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote_plus, unquote_plus

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import qrcode

from .config import settings
from .db import SessionLocal, init_db
from .models import Municipality, Role, Task, TaskStatus, User
from .services import (
    create_task,
    get_or_create_municipality,
    get_or_create_user,
    list_primary_units,
    list_tasks_for_period,
    list_users_in_primary_unit,
    task_is_available_for_municipality,
    task_stats,
)


def format_task(task: Task) -> str:
    starts_at = task.starts_at.strftime("%d.%m %H:%M")
    status_icon = "✅" if task.status == TaskStatus.COMPLETED else "🟢"
    return f"{status_icon} {task.title}\n📍 {task.location}\n🗓 {starts_at}\n{task.description}"


def main_menu_keyboard(role: Optional[Role]) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()

    if role == Role.ADMIN:
        builder.button(text="➕ Создать задачу")
        builder.button(text="📋 Задачи")
    elif role == Role.MUNICIPALITY:
        builder.button(text="📋 Мои задачи")
    else:
        builder.button(text="Выбрать роль")

    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def task_keyboard(task: Task) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Завершить" if task.status == TaskStatus.UPCOMING else "Открыть",
        callback_data=f"status:{task.id}",
    )
    builder.button(text="Статистика", callback_data=f"stats:{task.id}")
    builder.adjust(1)
    return builder.as_markup()


def municipality_task_keyboard(task: Task) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="QR-коды", callback_data=f"qr:{task.id}")
    builder.button(text="Статистика", callback_data=f"stats:{task.id}")
    builder.adjust(1)
    return builder.as_markup()


class TaskForm(StatesGroup):
    title = State()
    description = State()
    starts_at = State()
    location = State()
    municipalities = State()


async def get_current_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalars().first()


async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with SessionLocal() as session:
        user = await get_current_user(session, message.from_user.id)

    if not user:
        builder = InlineKeyboardBuilder()
        builder.button(text="Администратор", callback_data="role:admin")
        builder.button(text="Муниципалитет", callback_data="role:municipality")
        builder.adjust(2)
        await message.answer(
            "Привет! Выберите роль, чтобы продолжить:",
            reply_markup=builder.as_markup(),
        )
        return

    await message.answer(
        "Добро пожаловать! Используйте кнопки ниже для работы с ботом.",
        reply_markup=main_menu_keyboard(user.role),
    )


async def cmd_set_role(message: Message, state: FSMContext) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="Администратор", callback_data="role:admin")
    builder.button(text="Муниципалитет", callback_data="role:municipality")
    builder.adjust(2)
    await message.answer(
        "Выберите роль, чтобы продолжить:", reply_markup=builder.as_markup()
    )


async def process_municipality_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_role = data.get("target_role")
    if target_role != Role.MUNICIPALITY:
        await message.answer("Сначала выберите роль /set_role")
        return

    async with SessionLocal() as session:
        municipality = await get_or_create_municipality(session, message.text.strip())
        await get_or_create_user(
            session,
            message.from_user.id,
            message.from_user.full_name,
            Role.MUNICIPALITY,
            municipality=municipality,
        )
    await state.clear()
    await message.answer(
        "Роль 'municipality' сохранена. Используйте кнопки меню для просмотра задач.",
        reply_markup=main_menu_keyboard(Role.MUNICIPALITY),
    )


async def cmd_new_task(message: Message, state: FSMContext) -> None:
    async with SessionLocal() as session:
        user = await get_current_user(session, message.from_user.id)
    if not user or user.role != Role.ADMIN:
        await message.answer(
            "Доступно только администраторам. Нажмите 'Выбрать роль', чтобы указать доступ.",
            reply_markup=main_menu_keyboard(user.role if user else None),
        )
        return

    await state.clear()
    await state.set_state(TaskForm.title)
    await message.answer("Введите название задачи:")


async def task_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text)
    await state.set_state(TaskForm.description)
    await message.answer("Введите описание задачи:")


async def task_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text)
    await state.set_state(TaskForm.starts_at)
    await message.answer("Введите дату и время в формате ДД.ММ ЧЧ:ММ")


async def task_datetime(message: Message, state: FSMContext) -> None:
    try:
        starts_at = datetime.strptime(message.text, "%d.%m %H:%M")
        starts_at = starts_at.replace(year=datetime.utcnow().year)
    except ValueError:
        await message.answer("Неверный формат. Попробуйте еще раз: ДД.ММ ЧЧ:ММ")
        return

    await state.update_data(starts_at=starts_at)
    await state.set_state(TaskForm.location)
    await message.answer("Введите место проведения (текст):")


async def task_location(message: Message, state: FSMContext) -> None:
    await state.update_data(location=message.text)
    await state.set_state(TaskForm.municipalities)
    await message.answer("Перечислите муниципалитеты через запятую:")


async def finalize_task(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    municipality_names = [item.strip() for item in message.text.split(",") if item.strip()]
    if not municipality_names:
        await message.answer("Нужно указать хотя бы один муниципалитет")
        return

    async with SessionLocal() as session:
        municipality_objects: List[Municipality] = []
        for name in municipality_names:
            municipality_objects.append(await get_or_create_municipality(session, name))

        task = await create_task(
            session,
            title=data.get("title"),
            description=data.get("description"),
            starts_at=data.get("starts_at"),
            location=data.get("location"),
            municipalities=municipality_objects,
        )

    await state.clear()
    await message.answer(
        f"Задача '{task.title}' создана для: {', '.join(municipality_names)}",
        reply_markup=main_menu_keyboard(Role.ADMIN),
    )


async def cmd_tasks(message: Message) -> None:
    async with SessionLocal() as session:
        user = await get_current_user(session, message.from_user.id)

    if not user:
        await message.answer(
            "Сначала выберите роль через кнопку 'Выбрать роль'.",
            reply_markup=main_menu_keyboard(None),
        )
        return

    filter_municipalities: Optional[List[int]] = None
    if user.role == Role.MUNICIPALITY and user.municipality_id:
        filter_municipalities = [user.municipality_id]

    async with SessionLocal() as session:
        tasks = await list_tasks_for_period(session, municipality_ids=filter_municipalities)

    if not tasks:
        await message.answer("Задачи не найдены на ближайшую неделю.")
        return

    for task in tasks:
        kb = task_keyboard(task) if user.role == Role.ADMIN else municipality_task_keyboard(task)
        await message.answer(format_task(task), reply_markup=kb)


async def show_role_choice(message: Message, state: FSMContext) -> None:
    await cmd_set_role(message, state)


async def start_task_creation(message: Message, state: FSMContext) -> None:
    await cmd_new_task(message, state)


async def show_tasks_with_buttons(message: Message) -> None:
    await cmd_tasks(message)


async def show_stats(callback: CallbackQuery, task_id: int) -> None:
    async with SessionLocal() as session:
        task_obj = await session.get(Task, task_id)
        if not task_obj:
            await callback.message.answer("Задача не найдена")
            return
        stats = await task_stats(session, task_obj)

    lines = [f"Статистика по '{task_obj.title}':"]
    for name, attended, total in stats:
        lines.append(f"{name}: {attended}/{total}")
    await callback.message.answer("\n".join(lines))


async def change_status(callback: CallbackQuery, task_id: int) -> None:
    async with SessionLocal() as session:
        user = await get_current_user(session, callback.from_user.id)
        task_obj = await session.get(Task, task_id)
        if not task_obj:
            await callback.message.answer("Задача не найдена")
            return
        if not user or user.role != Role.ADMIN:
            await callback.answer("Только администратор может менять статус", show_alert=True)
            return

        new_status = TaskStatus.COMPLETED if task_obj.status == TaskStatus.UPCOMING else TaskStatus.UPCOMING
        task_obj.status = new_status
        await session.commit()

    await callback.answer("Статус обновлен")
    await callback.message.edit_text(format_task(task_obj), reply_markup=task_keyboard(task_obj))


async def set_role_from_callback(callback: CallbackQuery, role: Role, state: FSMContext) -> None:
    if role == Role.MUNICIPALITY:
        await state.update_data(target_role=role)
        await state.set_state(TaskForm.municipalities)
        await callback.message.answer("Введите название вашего муниципалитета:")
        await callback.answer()
        return

    async with SessionLocal() as session:
        await get_or_create_user(
            session, callback.from_user.id, callback.from_user.full_name, role
        )
    await state.clear()
    await callback.answer("Роль сохранена")
    await callback.message.answer(
        "Роль установлена. Используйте кнопки меню.",
        reply_markup=main_menu_keyboard(role),
    )


async def choose_primary_unit(callback: CallbackQuery, task_id: int) -> None:
    async with SessionLocal() as session:
        user = await get_current_user(session, callback.from_user.id)
        if not user or user.role != Role.MUNICIPALITY or not user.municipality_id:
            await callback.answer("Доступно только муниципалитету", show_alert=True)
            return

        is_available = await task_is_available_for_municipality(
            session, task_id, user.municipality_id
        )
        if not is_available:
            await callback.answer("Задача недоступна вашему муниципалитету", show_alert=True)
            return

        primary_units = await list_primary_units(session, user.municipality_id)

    if not primary_units:
        await callback.message.answer("Нет первичных отделений с участниками.")
        return

    builder = InlineKeyboardBuilder()
    for unit in primary_units:
        encoded = quote_plus(unit)
        builder.button(text=unit, callback_data=f"qrunit:{task_id}:{encoded}")
    builder.adjust(1)
    await callback.message.answer("Выберите первичное отделение:", reply_markup=builder.as_markup())


async def send_primary_unit_qr(callback: CallbackQuery, task_id: int, primary_unit: str) -> None:
    async with SessionLocal() as session:
        user = await get_current_user(session, callback.from_user.id)
        if not user or user.role != Role.MUNICIPALITY or not user.municipality_id:
            await callback.answer("Доступно только муниципалитету", show_alert=True)
            return

        is_available = await task_is_available_for_municipality(
            session, task_id, user.municipality_id
        )
        if not is_available:
            await callback.answer("Задача недоступна вашему муниципалитету", show_alert=True)
            return

        members = await list_users_in_primary_unit(session, user.municipality_id, primary_unit)

    if not members:
        await callback.message.answer("В этом отделении нет участников.")
        return

    await callback.message.answer(f"QR-коды для отделения '{primary_unit}':")

    for member in members:
        payload = f"task:{task_id}|user:{member.id}|name:{member.full_name}"
        qr_img = qrcode.make(payload)
        buffer = io.BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)
        photo = BufferedInputFile(buffer.read(), filename=f"qr_{member.id}.png")
        await callback.message.answer_photo(photo, caption=member.full_name)


async def handle_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.data:
        await callback.answer()
        return

    if callback.data.startswith("role:"):
        target = callback.data.split(":", 1)[1]
        role = Role.ADMIN if target == Role.ADMIN.value else Role.MUNICIPALITY
        await set_role_from_callback(callback, role, state)
        return

    if callback.data.startswith("stats:"):
        task_id = int(callback.data.split(":")[1])
        await show_stats(callback, task_id)
        return

    if callback.data.startswith("status:"):
        task_id = int(callback.data.split(":")[1])
        await change_status(callback, task_id)
        return

    if callback.data.startswith("qrunit:"):
        _, task_id, encoded_unit = callback.data.split(":", 2)
        primary_unit = unquote_plus(encoded_unit)
        await send_primary_unit_qr(callback, int(task_id), primary_unit)
        return

    if callback.data.startswith("qr:"):
        task_id = int(callback.data.split(":")[1])
        await choose_primary_unit(callback, task_id)
        return

    await callback.answer()


async def main() -> None:
    await init_db()
    bot = Bot(token=settings.bot_token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_set_role, Command("set_role"))
    dp.message.register(cmd_new_task, Command("new_task"))
    dp.message.register(cmd_tasks, Command("tasks"))

    dp.message.register(show_role_choice, F.text == "Выбрать роль")
    dp.message.register(start_task_creation, F.text == "➕ Создать задачу")
    dp.message.register(show_tasks_with_buttons, F.text.in_({"📋 Задачи", "📋 Мои задачи"}))

    dp.message.register(task_title, TaskForm.title)
    dp.message.register(task_description, TaskForm.description)
    dp.message.register(task_datetime, TaskForm.starts_at)
    dp.message.register(task_location, TaskForm.location)
    dp.message.register(process_municipality_name, TaskForm.municipalities, F.text)
    dp.message.register(finalize_task, TaskForm.municipalities)

    dp.callback_query.register(handle_callback)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
