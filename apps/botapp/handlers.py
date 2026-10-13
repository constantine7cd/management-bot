from datetime import date, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.attendance.models import CorrectionRequest, Shift
from apps.attendance.services import AttendanceError, end_shift, start_shift
from apps.auditlog.models import record
from apps.incidents.models import Incident
from apps.leave_management.models import LeaveType
from apps.leave_management.services import LeaveError, create_leave_request
from apps.organization.models import Company, Employee, EmployeeInvite, Workplace, WorkplaceShift
from apps.supervisor_documents.models import SupervisorDocument
from apps.supervisor_documents.services import (
    MAX_DOCUMENT_SIZE,
    SupervisorDocumentError,
    append_supervisor_document_version,
    can_supervisor_reupload,
    create_supervisor_document,
)

from .texts import TEXTS, t

router = Router()


class NavigationResetMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        text = getattr(event, "text", None)
        navigation_texts = {
            value
            for language in TEXTS.values()
            for key, value in language.items()
            if key
            in {
                "start",
                "end",
                "stats",
                "leave",
                "correction",
                "issue",
                "upload_document",
                "update_document",
                "language",
                "cancel",
            }
        }
        if text in navigation_texts and data.get("state"):
            await data["state"].clear()
        return await handler(event, data)


router.message.outer_middleware(NavigationResetMiddleware())


class Form(StatesGroup):
    choosing_work_shift = State()
    waiting_start_location = State()
    waiting_end_location = State()
    stats_range = State()
    leave_type = State()
    leave_start_date = State()
    leave_end_date = State()
    leave_reason = State()
    correction_shift = State()
    correction_field = State()
    correction_datetime = State()
    correction_reason = State()
    incident_severity = State()
    incident_category = State()
    incident_description = State()
    incident_evidence = State()
    document_kind = State()
    document_workplace = State()
    document_description = State()
    document_update_kind = State()
    document_update_choice = State()
    document_file = State()


def menu(language, category=Employee.Category.OPERATIONS):
    if category == Employee.Category.SUPERVISOR:
        return ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text=t(language, "upload_document")),
                    KeyboardButton(text=t(language, "update_document")),
                ],
                [
                    KeyboardButton(text=t(language, "leave")),
                    KeyboardButton(text=t(language, "issue")),
                ],
                [KeyboardButton(text=t(language, "language"))],
                [KeyboardButton(text=t(language, "cancel"))],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(language, "start")), KeyboardButton(text=t(language, "end"))],
            [KeyboardButton(text=t(language, "stats")), KeyboardButton(text=t(language, "leave"))],
            [KeyboardButton(text=t(language, "correction")), KeyboardButton(text=t(language, "issue"))],
            [KeyboardButton(text=t(language, "language"))],
            [KeyboardButton(text=t(language, "cancel"))],
        ],
        resize_keyboard=True,
    )


def location_keyboard(language):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(language, "share_location"), request_location=True)],
            [KeyboardButton(text=t(language, "cancel"))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@sync_to_async
def employee_for(user_id):
    return Employee.objects.filter(telegram_user_id=user_id, is_active=True).first()


@sync_to_async
@transaction.atomic
def consume_invite(token, message):
    invite = EmployeeInvite.objects.select_for_update().select_related("employee").filter(token=token).first()
    if not invite or not invite.is_valid or not invite.employee.is_active:
        return None
    existing = (
        Employee.objects.filter(
            telegram_user_id=message.from_user.id,
            is_active=True,
        )
        .exclude(pk=invite.employee_id)
        .exists()
    )
    if existing or invite.employee.telegram_user_id not in (None, message.from_user.id):
        return None
    employee = invite.employee
    employee.telegram_user_id = message.from_user.id
    employee.telegram_chat_id = message.chat.id
    employee.telegram_username = message.from_user.username or ""
    employee.save(update_fields=["telegram_user_id", "telegram_chat_id", "telegram_username"])
    invite.used_at = timezone.now()
    invite.save(update_fields=["used_at"])
    record("employee.telegram_linked", employee, employee_actor_id=employee.id)
    return employee


@router.message(CommandStart())
async def command_start(message: Message, command):
    employee = await employee_for(message.from_user.id)
    if not employee and command.args:
        employee = await consume_invite(command.args, message)
    if not employee:
        await message.answer(t("en", "not_linked"))
        return
    await message.answer(
        t(employee.language, "welcome", name=employee.first_name),
        reply_markup=menu(employee.language, employee.category),
    )


async def require_employee(message):
    employee = await employee_for(message.from_user.id)
    if not employee:
        await message.answer(t("en", "not_linked"))
    return employee


async def require_operations_employee(message):
    employee = await require_employee(message)
    if employee and employee.category != Employee.Category.OPERATIONS:
        await message.answer(
            t(employee.language, "operations_only"),
            reply_markup=menu(employee.language, employee.category),
        )
        return None
    return employee


async def require_supervisor(message):
    employee = await require_employee(message)
    if employee and employee.category != Employee.Category.SUPERVISOR:
        await message.answer(
            t(employee.language, "supervisors_only"),
            reply_markup=menu(employee.language, employee.category),
        )
        return None
    return employee


@router.message(F.text.in_([TEXTS["en"]["cancel"], TEXTS["ru"]["cancel"]]))
async def cancel_action(message: Message, state: FSMContext):
    employee = await require_employee(message)
    await state.clear()
    if employee:
        await message.answer(
            t(employee.language, "cancelled"),
            reply_markup=menu(employee.language, employee.category),
        )


@sync_to_async
def start_options(employee):
    has_open_shift = employee.shifts.filter(ended_at__isnull=True).exists()
    shifts = list(
        WorkplaceShift.objects.filter(
            workplace__employees=employee,
            workplace__is_active=True,
            is_active=True,
        )
        .select_related("workplace")
        .distinct()
        .order_by("workplace__name", "number")
    )
    return has_open_shift, shifts


@sync_to_async
def open_shift_exists(employee):
    return employee.shifts.filter(ended_at__isnull=True).exists()


@sync_to_async
def employee_local_date(employee):
    timezone_name = (
        employee.workplaces.filter(is_active=True)
        .values_list("timezone", flat=True)
        .first()
        or "Europe/Chisinau"
    )
    return timezone.now().astimezone(ZoneInfo(timezone_name)).date()


@router.message(F.text.in_([TEXTS["en"]["start"], TEXTS["ru"]["start"]]))
async def ask_start(message: Message, state: FSMContext):
    employee = await require_operations_employee(message)
    if employee:
        has_open_shift, workplace_shifts = await start_options(employee)
        if has_open_shift:
            await message.answer(t(employee.language, "already_working"))
            return
        if not workplace_shifts:
            await message.answer(t(employee.language, "no_work_shifts"))
            return
        buttons = []
        for workplace_shift in workplace_shifts:
            label = workplace_shift.name or (
                f"Shift {workplace_shift.number}"
                if employee.language == "en"
                else f"Смена {workplace_shift.number}"
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"{workplace_shift.workplace.name} · {label}",
                        callback_data=f"workshift:{workplace_shift.id}",
                    )
                ]
            )
        await state.set_state(Form.choosing_work_shift)
        await message.answer(
            t(employee.language, "choose_work_shift"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(Form.choosing_work_shift, F.data.startswith("workshift:"))
async def choose_work_shift(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    if (
        not employee
        or employee.category != Employee.Category.OPERATIONS
        or await open_shift_exists(employee)
    ):
        await state.clear()
        if not employee:
            error_text = t("en", "not_linked")
        elif employee.category != Employee.Category.OPERATIONS:
            error_text = t(employee.language, "operations_only")
        else:
            error_text = t(employee.language, "already_working")
        await callback.answer(
            error_text,
            show_alert=True,
        )
        return
    shift_id = int(callback.data.rsplit(":", 1)[1])
    allowed = await sync_to_async(
        employee.workplaces.filter(daily_shifts__id=shift_id, daily_shifts__is_active=True).exists
    )()
    if not allowed:
        await callback.answer(t(employee.language, "no_work_shifts"), show_alert=True)
        return
    await state.update_data(workplace_shift_id=shift_id)
    await state.set_state(Form.waiting_start_location)
    await callback.message.answer(
        t(employee.language, "location_prompt"),
        reply_markup=location_keyboard(employee.language),
    )
    await callback.answer()


@router.message(F.text.in_([TEXTS["en"]["end"], TEXTS["ru"]["end"]]))
async def ask_end(message: Message, state: FSMContext):
    employee = await require_operations_employee(message)
    if employee:
        if not await open_shift_exists(employee):
            await message.answer(
                t(employee.language, "not_working"),
                reply_markup=menu(employee.language, employee.category),
            )
            return
        await state.set_state(Form.waiting_end_location)
        await message.answer(t(employee.language, "location_prompt"), reply_markup=location_keyboard(employee.language))


@router.message(Form.waiting_start_location, F.location)
async def location_start(message: Message, state: FSMContext):
    employee = await require_operations_employee(message)
    if not employee:
        await state.clear()
        return
    data = await state.get_data()
    try:
        shift = await sync_to_async(start_shift)(
            employee.id,
            message.location.latitude,
            message.location.longitude,
            message.location.horizontal_accuracy,
            message.date,
            data.get("workplace_shift_id"),
        )
        await message.answer(
            t(
                employee.language,
                "shift_started",
                time=shift.local_started_at.strftime("%H:%M"),
                workplace=shift.start_workplace.name,
            ),
            reply_markup=menu(employee.language, employee.category),
        )
        await state.clear()
    except AttendanceError as error:
        await message.answer(
            t(employee.language, "location_error", error=str(error)),
            reply_markup=menu(employee.language, employee.category),
        )


@router.message(Form.waiting_end_location, F.location)
async def location_end(message: Message, state: FSMContext):
    employee = await require_operations_employee(message)
    if not employee:
        await state.clear()
        return
    try:
        shift = await sync_to_async(end_shift)(employee.id, message.location.latitude, message.location.longitude, message.location.horizontal_accuracy, message.date)
        hours = shift.duration.total_seconds() / 3600
        await message.answer(
            t(
                employee.language,
                "shift_ended",
                time=shift.local_ended_at.strftime("%H:%M"),
                hours=hours,
            ),
            reply_markup=menu(employee.language, employee.category),
        )
        await state.clear()
    except AttendanceError as error:
        await message.answer(
            t(employee.language, "location_error", error=str(error)),
            reply_markup=menu(employee.language, employee.category),
        )


@sync_to_async
def stats(employee):
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    completed = employee.shifts.filter(ended_at__isnull=False)
    def hours(start):
        total = sum((s.duration.total_seconds() for s in completed.filter(started_at__date__gte=start)), 0)
        return total / 3600
    return hours(week_start), hours(month_start), employee.shifts.filter(ended_at__isnull=True).exists()


@router.message(F.text.in_([TEXTS["en"]["stats"], TEXTS["ru"]["stats"]]))
async def show_stats(message: Message):
    employee = await require_operations_employee(message)
    if employee:
        labels = ("Week", "Month", "Custom") if employee.language == "en" else ("Неделя", "Месяц", "Период")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=labels[0], callback_data="stats:week"),
            InlineKeyboardButton(text=labels[1], callback_data="stats:month"),
            InlineKeyboardButton(text=labels[2], callback_data="stats:custom"),
        ]])
        await message.answer(t(employee.language, "menu"), reply_markup=keyboard)


@sync_to_async
def range_hours(employee, start, end):
    shifts = employee.shifts.filter(
        ended_at__isnull=False,
        started_at__date__gte=start - timedelta(days=1),
        started_at__date__lte=end + timedelta(days=1),
    )
    return sum(
        (
            shift.duration.total_seconds()
            for shift in shifts
            if start <= shift.local_started_at.date() <= end
        ),
        0,
    ) / 3600


@router.callback_query(F.data.in_(["stats:week", "stats:month"]))
async def stats_period(callback: CallbackQuery):
    employee = await employee_for(callback.from_user.id)
    if not employee or employee.category != Employee.Category.OPERATIONS:
        await callback.answer(t("en", "not_linked"), show_alert=True)
        return
    today = await employee_local_date(employee)
    start = today - timedelta(days=today.weekday()) if callback.data == "stats:week" else today.replace(day=1)
    hours = await range_hours(employee, start, today)
    await callback.message.answer(f"{start} — {today}: {hours:.2f} h")
    await callback.answer()


@router.callback_query(F.data == "stats:custom")
async def stats_custom(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    if not employee or employee.category != Employee.Category.OPERATIONS:
        return
    await state.set_state(Form.stats_range)
    prompt = "Send start and end dates: 2026-10-01, 2026-10-31" if employee.language == "en" else "Отправьте даты начала и конца: 2026-10-01, 2026-10-31"
    await callback.message.answer(prompt)
    await callback.answer()


@router.message(Form.stats_range, F.text)
async def stats_custom_result(message: Message, state: FSMContext):
    employee = await require_operations_employee(message)
    if not employee:
        await state.clear()
        return
    try:
        start_text, end_text = [part.strip() for part in message.text.split(",", 1)]
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        if end < start or (end - start).days > 366:
            raise ValueError
        hours = await range_hours(employee, start, end)
        await state.clear()
        await message.answer(
            f"{start} — {end}: {hours:.2f} h",
            reply_markup=menu(employee.language, employee.category),
        )
    except Exception:
        await message.answer(t(employee.language, "bad_format"))


@router.message(F.text.in_([TEXTS["en"]["language"], TEXTS["ru"]["language"]]))
async def change_language(message: Message):
    employee = await require_employee(message)
    if employee:
        employee.language = "ru" if employee.language == "en" else "en"
        await sync_to_async(employee.save)(update_fields=["language"])
        await message.answer(
            t(employee.language, "menu"),
            reply_markup=menu(employee.language, employee.category),
        )


def date_keyboard(language):
    labels = ("Today", "Tomorrow", "Next Monday") if language == "en" else (
        "Сегодня",
        "Завтра",
        "Следующий понедельник",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels[0], callback_data="leave:start:today")],
            [InlineKeyboardButton(text=labels[1], callback_data="leave:start:tomorrow")],
            [InlineKeyboardButton(text=labels[2], callback_data="leave:start:monday")],
        ]
    )


def end_date_keyboard(language):
    labels = ("Same day", "+1 day", "+3 days", "+7 days", "Enter date") if language == "en" else (
        "Один день",
        "+1 день",
        "+3 дня",
        "+7 дней",
        "Ввести дату",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=labels[index], callback_data=f"leave:end:{days}")]
            for index, days in enumerate((0, 1, 3, 7))
        ]
        + [[InlineKeyboardButton(text=labels[4], callback_data="leave:end:custom")]]
    )


async def prompt_leave_end(target, state, employee, start_date):
    await state.update_data(leave_start=str(start_date))
    await state.set_state(Form.leave_end_date)
    await target.answer(
        t(employee.language, "choose_end_date"), reply_markup=end_date_keyboard(employee.language)
    )


@router.message(F.text.in_([TEXTS["en"]["leave"], TEXTS["ru"]["leave"]]))
async def ask_leave(message: Message, state: FSMContext):
    employee = await require_employee(message)
    if not employee:
        return
    leave_types = await sync_to_async(list)(LeaveType.objects.filter(is_active=True))
    if not leave_types:
        await message.answer("No leave types are configured." if employee.language == "en" else "Типы отсутствий не настроены.")
        return
    buttons = [
        [InlineKeyboardButton(
            text=item.name_ru if employee.language == "ru" else item.name_en,
            callback_data=f"leave:type:{item.id}",
        )]
        for item in leave_types
    ]
    await state.set_state(Form.leave_type)
    await message.answer(
        t(employee.language, "choose_leave_type"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(Form.leave_type, F.data.startswith("leave:type:"))
async def choose_leave_type(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    leave_type_id = int(callback.data.rsplit(":", 1)[1])
    exists = await sync_to_async(LeaveType.objects.filter(pk=leave_type_id, is_active=True).exists)()
    if not exists:
        await callback.answer("This leave type is no longer available.", show_alert=True)
        return
    await state.update_data(leave_type_id=leave_type_id)
    await state.set_state(Form.leave_start_date)
    await callback.message.answer(
        t(employee.language, "choose_start_date"), reply_markup=date_keyboard(employee.language)
    )
    await callback.answer()


@router.callback_query(Form.leave_start_date, F.data.startswith("leave:start:"))
async def choose_leave_start(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    choice = callback.data.rsplit(":", 1)[1]
    selected = await employee_local_date(employee)
    if choice == "tomorrow":
        selected += timedelta(days=1)
    elif choice == "monday":
        selected += timedelta(days=(7 - selected.weekday()) % 7 or 7)
    await prompt_leave_end(callback.message, state, employee, selected)
    await callback.answer()


@router.message(Form.leave_start_date, F.text)
async def type_leave_start(message: Message, state: FSMContext):
    employee = await require_employee(message)
    try:
        selected = date.fromisoformat(message.text.strip())
        await prompt_leave_end(message, state, employee, selected)
    except ValueError:
        await message.answer(t(employee.language, "date_invalid"))


@router.callback_query(Form.leave_end_date, F.data.startswith("leave:end:"))
async def choose_leave_end(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    choice = callback.data.rsplit(":", 1)[1]
    if choice == "custom":
        await callback.message.answer(t(employee.language, "choose_end_date"))
        await callback.answer()
        return
    data = await state.get_data()
    selected = date.fromisoformat(data["leave_start"]) + timedelta(days=int(choice))
    await state.update_data(leave_end=str(selected))
    await state.set_state(Form.leave_reason)
    await callback.message.answer(t(employee.language, "leave_reason"))
    await callback.answer()


@router.message(Form.leave_end_date, F.text)
async def type_leave_end(message: Message, state: FSMContext):
    employee = await require_employee(message)
    data = await state.get_data()
    try:
        selected = date.fromisoformat(message.text.strip())
    except ValueError:
        await message.answer(t(employee.language, "date_invalid"))
        return
    if selected < date.fromisoformat(data["leave_start"]):
        await message.answer(t(employee.language, "date_before_start"))
        return
    await state.update_data(leave_end=str(selected))
    await state.set_state(Form.leave_reason)
    await message.answer(t(employee.language, "leave_reason"))


@router.message(Form.leave_reason, F.text)
async def save_leave(message: Message, state: FSMContext):
    employee = await require_employee(message)
    data = await state.get_data()
    try:
        leave_type = await sync_to_async(LeaveType.objects.get)(pk=data["leave_type_id"], is_active=True)
        request = await sync_to_async(create_leave_request)(
            employee,
            leave_type,
            date.fromisoformat(data["leave_start"]),
            date.fromisoformat(data["leave_end"]),
            "" if message.text.strip() == "-" else message.text.strip(),
        )
    except LeaveError as error:
        key = "leave_overlap" if "overlap" in str(error).lower() else "bad_format"
        await message.answer(t(employee.language, key))
        return
    await state.clear()
    await message.answer(
        t(employee.language, "leave_saved", id=request.id),
        reply_markup=menu(employee.language, employee.category),
    )


@router.message(F.text.in_([TEXTS["en"]["correction"], TEXTS["ru"]["correction"]]))
async def ask_correction(message: Message, state: FSMContext):
    employee = await require_operations_employee(message)
    if not employee:
        return
    shifts = await sync_to_async(list)(
        Shift.objects.filter(employee=employee).select_related("workplace_shift")[:10]
    )
    if not shifts:
        await message.answer("You have no shifts to correct." if employee.language == "en" else "У вас нет смен для исправления.")
        return
    buttons = []
    for shift in shifts:
        number = f" · #{shift.workplace_shift.number}" if shift.workplace_shift else ""
        buttons.append([InlineKeyboardButton(
            text=f"{shift.local_started_at:%Y-%m-%d %H:%M}{number}",
            callback_data=f"correction:shift:{shift.id}",
        )])
    await state.set_state(Form.correction_shift)
    await message.answer(
        t(employee.language, "choose_shift_correction"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(Form.correction_shift, F.data.startswith("correction:shift:"))
async def choose_correction_shift(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    shift_id = int(callback.data.rsplit(":", 1)[1])
    exists = await sync_to_async(Shift.objects.filter(pk=shift_id, employee=employee).exists)()
    if not exists:
        await callback.answer("Shift not found.", show_alert=True)
        return
    labels = ("Start time", "End time") if employee.language == "en" else (
        "Начало",
        "Окончание",
    )
    await state.update_data(correction_shift_id=shift_id)
    await state.set_state(Form.correction_field)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=labels[0], callback_data="correction:field:start"),
        InlineKeyboardButton(text=labels[1], callback_data="correction:field:end"),
    ]])
    await callback.message.answer(t(employee.language, "choose_correction_field"), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(Form.correction_field, F.data.startswith("correction:field:"))
async def choose_correction_field(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    field = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    if "correction_shift_id" not in data or field not in {"start", "end"}:
        await callback.answer("Correction session expired.", show_alert=True)
        return
    await state.update_data(correction_field=field)
    await state.set_state(Form.correction_datetime)
    await callback.message.answer(t(employee.language, "correction_datetime", field=field))
    await callback.answer()


@router.message(Form.correction_datetime, F.text)
async def enter_correction_datetime(message: Message, state: FSMContext):
    employee = await require_employee(message)
    data = await state.get_data()
    try:
        shift = await sync_to_async(Shift.objects.get)(pk=data["correction_shift_id"], employee=employee)
        value = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo(shift.start_timezone)
        )
    except (ValueError, Shift.DoesNotExist):
        await message.answer(t(employee.language, "datetime_invalid"))
        return
    if (data["correction_field"] == "start" and shift.ended_at and value >= shift.ended_at) or (
        data["correction_field"] == "end" and value <= shift.started_at
    ):
        await message.answer(t(employee.language, "correction_invalid_order"))
        return
    await state.update_data(correction_value=value.isoformat())
    await state.set_state(Form.correction_reason)
    await message.answer(t(employee.language, "correction_reason"))


@sync_to_async
@transaction.atomic
def create_correction(employee, data, reason):
    if employee.category != Employee.Category.OPERATIONS:
        raise ValueError("operations_only")
    shift = Shift.objects.select_for_update().get(pk=data["correction_shift_id"], employee=employee)
    if shift.corrections.filter(status=CorrectionRequest.Status.PENDING).exists():
        raise ValueError("pending")
    value = datetime.fromisoformat(data["correction_value"])
    correction = CorrectionRequest(
        shift=shift,
        requested_start=value if data["correction_field"] == "start" else None,
        requested_end=value if data["correction_field"] == "end" else None,
        reason=reason,
    )
    correction.full_clean()
    correction.save()
    record("attendance.correction_requested", correction, employee_actor_id=employee.id)
    return correction


@router.message(Form.correction_reason, F.text)
async def save_correction(message: Message, state: FSMContext):
    employee = await require_employee(message)
    try:
        correction = await create_correction(employee, await state.get_data(), message.text.strip())
    except ValueError:
        text = "A correction for this shift is already pending." if employee.language == "en" else "Исправление этой смены уже ожидает рассмотрения."
        await message.answer(text)
        return
    await state.clear()
    await message.answer(
        t(employee.language, "correction_saved", id=correction.id),
        reply_markup=menu(employee.language, employee.category),
    )


@router.message(F.text.in_([TEXTS["en"]["issue"], TEXTS["ru"]["issue"]]))
async def ask_issue(message: Message, state: FSMContext):
    employee = await require_employee(message)
    if not employee:
        return
    labels = {
        "en": ("Low", "Normal", "High", "Emergency"),
        "ru": ("Низкая", "Обычная", "Высокая", "ЧП"),
    }[employee.language]
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"incident:severity:{value}")]
               for label, value in zip(labels, Incident.Severity.values, strict=True)]
    await state.set_state(Form.incident_severity)
    await message.answer(
        t(employee.language, "choose_severity"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(Form.incident_severity, F.data.startswith("incident:severity:"))
async def choose_incident_severity(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    severity = callback.data.rsplit(":", 1)[1]
    if severity not in Incident.Severity.values:
        await callback.answer("Invalid severity.", show_alert=True)
        return
    categories = {
        "en": ("Safety", "Equipment", "Workplace", "Other"),
        "ru": ("Безопасность", "Оборудование", "Рабочее место", "Другое"),
    }[employee.language]
    await state.update_data(incident_severity=severity)
    await state.set_state(Form.incident_category)
    buttons = [[InlineKeyboardButton(text=label, callback_data=f"incident:category:{key}")]
               for label, key in zip(categories, ("safety", "equipment", "workplace", "other"), strict=True)]
    await callback.message.answer(
        t(employee.language, "choose_category"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(Form.incident_category, F.data.startswith("incident:category:"))
async def choose_incident_category(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    category = callback.data.rsplit(":", 1)[1]
    if category not in {"safety", "equipment", "workplace", "other"}:
        await callback.answer("Invalid category.", show_alert=True)
        return
    await state.update_data(incident_category=category)
    await state.set_state(Form.incident_description)
    await callback.message.answer(t(employee.language, "incident_description"))
    await callback.answer()


@router.message(Form.incident_description, F.text)
async def describe_incident(message: Message, state: FSMContext):
    employee = await require_employee(message)
    description = message.text.strip()
    if len(description) < 5:
        await message.answer("Please provide at least five characters." if employee.language == "en" else "Опишите проблему хотя бы пятью символами.")
        return
    data = await state.get_data()
    incident = await sync_to_async(Incident.objects.create)(
        employee=employee,
        severity=data["incident_severity"],
        category=data["incident_category"],
        description=description,
    )
    await state.update_data(incident_id=incident.id)
    await state.set_state(Form.incident_evidence)
    await message.answer(
        t(employee.language, "incident_evidence"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t(employee.language, "skip"), callback_data="incident:evidence:skip")
        ]]),
    )


async def finalize_incident(message, state, employee, incident):
    await sync_to_async(record)(
        "incident.created",
        incident,
        employee_actor_id=employee.id,
        metadata={"severity": incident.severity},
    )
    if incident.severity == Incident.Severity.EMERGENCY:
        from apps.accounts.models import User

        manager_ids = await sync_to_async(list)(
            User.objects.filter(
                Q(is_superuser=True) | Q(role=User.Role.ADMIN) | Q(managed_teams=employee.team),
                telegram_chat_id__isnull=False,
                is_active=True,
            ).values_list("telegram_chat_id", flat=True).distinct()
        )
        for chat_id in manager_ids:
            await message.bot.send_message(
                chat_id,
                f"EMERGENCY #{incident.id}: {employee} — {incident.category}\n{incident.description}",
            )
    await state.clear()
    await message.answer(
        t(employee.language, "issue_saved", id=incident.id),
        reply_markup=menu(employee.language, employee.category),
    )


@router.callback_query(Form.incident_evidence, F.data == "incident:evidence:skip")
async def skip_incident_evidence(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    data = await state.get_data()
    incident = await sync_to_async(Incident.objects.get)(pk=data["incident_id"], employee=employee)
    await finalize_incident(callback.message, state, employee, incident)
    await callback.answer()


@router.message(Form.incident_evidence, F.location)
async def add_incident_location(message: Message, state: FSMContext):
    employee = await require_employee(message)
    data = await state.get_data()
    incident = await sync_to_async(Incident.objects.get)(pk=data["incident_id"], employee=employee)
    incident.latitude = message.location.latitude
    incident.longitude = message.location.longitude
    await sync_to_async(incident.save)(update_fields=["latitude", "longitude"])
    await finalize_incident(message, state, employee, incident)


@router.message(Form.incident_evidence, F.photo)
async def add_incident_photo(message: Message, state: FSMContext):
    employee = await require_employee(message)
    data = await state.get_data()
    incident = await sync_to_async(Incident.objects.get)(pk=data["incident_id"], employee=employee)
    telegram_file = await message.bot.get_file(message.photo[-1].file_id)
    buffer = BytesIO()
    await message.bot.download(telegram_file, destination=buffer)
    await sync_to_async(incident.attachment.save)(
        f"telegram-{message.photo[-1].file_unique_id}.jpg",
        ContentFile(buffer.getvalue()),
    )
    await finalize_incident(message, state, employee, incident)


def document_kind_label(language, kind):
    return {
        "en": {
            SupervisorDocument.Kind.DAILY_TIMESHEET: "Daily timesheet",
            SupervisorDocument.Kind.DELIVERY_NOTE: "Delivery note",
            SupervisorDocument.Kind.MONTHLY_INVENTORY: "Monthly inventory",
        },
        "ru": {
            SupervisorDocument.Kind.DAILY_TIMESHEET: "Ежедневный табель",
            SupervisorDocument.Kind.DELIVERY_NOTE: "Товарная накладная",
            SupervisorDocument.Kind.MONTHLY_INVENTORY: "Ежемесячная инвентаризация",
        },
    }[language][kind]


def document_kind_keyboard(language, prefix):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=document_kind_label(language, kind),
                    callback_data=f"supdoc:{prefix}:{kind}"
                )
            ]
            for kind in SupervisorDocument.Kind.values
        ]
    )


@sync_to_async
def supervisor_workplaces(employee):
    return list(employee.workplaces.filter(is_active=True).order_by("name"))


@sync_to_async
def document_slot_exists(workplace_id, kind):
    if kind == SupervisorDocument.Kind.DELIVERY_NOTE:
        return False
    workplace = Workplace.objects.get(pk=workplace_id)
    local_date = timezone.now().astimezone(ZoneInfo(workplace.timezone)).date()
    if kind == SupervisorDocument.Kind.MONTHLY_INVENTORY:
        local_date = local_date.replace(day=1)
    return SupervisorDocument.objects.filter(
        workplace=workplace, kind=kind, document_date=local_date
    ).exists()


@sync_to_async
def update_document_options(employee, kind):
    edit_days = Company.objects.values_list(
        "supervisor_document_edit_days", flat=True
    ).first()
    edit_days = 2 if edit_days is None else edit_days
    if edit_days <= 0:
        return []
    documents = list(
        SupervisorDocument.objects.filter(
            workplace__employees=employee,
            kind=kind,
        )
        .select_related("workplace")
        .distinct()[:50]
    )
    return [
        document
        for document in documents
        if can_supervisor_reupload(document, edit_days=edit_days)
    ][:20]


@router.message(
    F.text.in_([TEXTS["en"]["upload_document"], TEXTS["ru"]["upload_document"]])
)
async def ask_document_upload(message: Message, state: FSMContext):
    employee = await require_supervisor(message)
    if not employee:
        return
    await state.set_state(Form.document_kind)
    await message.answer(
        t(employee.language, "choose_document_type"),
        reply_markup=document_kind_keyboard(employee.language, "newkind"),
    )


@router.callback_query(Form.document_kind, F.data.startswith("supdoc:newkind:"))
async def choose_document_kind(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    kind = callback.data.rsplit(":", 1)[1]
    if (
        not employee
        or employee.category != Employee.Category.SUPERVISOR
        or kind not in SupervisorDocument.Kind.values
    ):
        await callback.answer(t("en", "supervisors_only"), show_alert=True)
        return
    workplaces = await supervisor_workplaces(employee)
    if not workplaces:
        await state.clear()
        await callback.message.answer(
            t(employee.language, "no_document_workplaces"),
            reply_markup=menu(employee.language, employee.category),
        )
        await callback.answer()
        return
    await state.update_data(document_mode="new", document_kind=kind)
    await state.set_state(Form.document_workplace)
    await callback.message.answer(
        t(employee.language, "choose_document_workplace"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=workplace.name,
                        callback_data=f"supdoc:workplace:{workplace.pk}",
                    )
                ]
                for workplace in workplaces
            ]
        ),
    )
    await callback.answer()


@router.callback_query(Form.document_workplace, F.data.startswith("supdoc:workplace:"))
async def choose_document_workplace(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    workplace_id = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    allowed = bool(
        employee
        and employee.category == Employee.Category.SUPERVISOR
        and await sync_to_async(employee.workplaces.filter(pk=workplace_id, is_active=True).exists)()
    )
    if not allowed:
        await callback.answer(t("en", "no_document_workplaces"), show_alert=True)
        return
    kind = data.get("document_kind")
    if await document_slot_exists(workplace_id, kind):
        await state.clear()
        await callback.message.answer(
            t(employee.language, "document_already_exists"),
            reply_markup=menu(employee.language, employee.category),
        )
        await callback.answer()
        return
    await state.update_data(document_workplace_id=workplace_id)
    if kind == SupervisorDocument.Kind.DELIVERY_NOTE:
        await state.set_state(Form.document_description)
        await callback.message.answer(t(employee.language, "delivery_description"))
    else:
        await state.set_state(Form.document_file)
        await callback.message.answer(
            t(
                employee.language,
                "send_document_file",
                kind=document_kind_label(employee.language, kind),
            )
        )
    await callback.answer()


@router.message(Form.document_description, F.text)
async def enter_delivery_description(message: Message, state: FSMContext):
    employee = await require_supervisor(message)
    if not employee:
        await state.clear()
        return
    description = message.text.strip()
    if not description or len(description) > 300:
        await message.answer(t(employee.language, "delivery_description_invalid"))
        return
    await state.update_data(document_description=description)
    await state.set_state(Form.document_file)
    await message.answer(
        t(
            employee.language,
            "send_document_file",
            kind=document_kind_label(
                employee.language, SupervisorDocument.Kind.DELIVERY_NOTE
            ),
        )
    )


@router.message(
    F.text.in_([TEXTS["en"]["update_document"], TEXTS["ru"]["update_document"]])
)
async def ask_document_update(message: Message, state: FSMContext):
    employee = await require_supervisor(message)
    if not employee:
        return
    await state.set_state(Form.document_update_kind)
    await message.answer(
        t(employee.language, "choose_update_document_type"),
        reply_markup=document_kind_keyboard(employee.language, "updatekind"),
    )


@router.callback_query(
    Form.document_update_kind, F.data.startswith("supdoc:updatekind:")
)
async def choose_update_document_kind(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    kind = callback.data.rsplit(":", 1)[1]
    if (
        not employee
        or employee.category != Employee.Category.SUPERVISOR
        or kind not in SupervisorDocument.Kind.values
    ):
        await callback.answer(t("en", "supervisors_only"), show_alert=True)
        return
    documents = await update_document_options(employee, kind)
    if not documents:
        await state.clear()
        await callback.message.answer(
            t(employee.language, "no_documents_to_update"),
            reply_markup=menu(employee.language, employee.category),
        )
        await callback.answer()
        return
    await state.set_state(Form.document_update_choice)
    await callback.message.answer(
        t(employee.language, "choose_document_to_update"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=(
                            f"{document.document_date} · {document.workplace.name}"
                            + (
                                f" · #{document.delivery_number} · {document.description[:35]}"
                                if document.delivery_number
                                else ""
                            )
                        ),
                        callback_data=f"supdoc:update:{document.pk}",
                    )
                ]
                for document in documents
            ]
        ),
    )
    await callback.answer()


@router.callback_query(Form.document_update_choice, F.data.startswith("supdoc:update:"))
async def choose_document_to_update(callback: CallbackQuery, state: FSMContext):
    employee = await employee_for(callback.from_user.id)
    if not employee or employee.category != Employee.Category.SUPERVISOR:
        await callback.answer(t("en", "supervisors_only"), show_alert=True)
        return
    document_id = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(document_mode="update", document_id=document_id)
    await state.set_state(Form.document_file)
    await callback.message.answer(t(employee.language, "send_replacement_file"))
    await callback.answer()


async def telegram_document_payload(message):
    if message.document:
        item = message.document
        filename = item.file_name or f"telegram-{item.file_unique_id}"
        mime_type = item.mime_type or "application/octet-stream"
    else:
        item = message.photo[-1]
        filename = f"telegram-{item.file_unique_id}.jpg"
        mime_type = "image/jpeg"
    if item.file_size and item.file_size > MAX_DOCUMENT_SIZE:
        raise SupervisorDocumentError("The file is larger than 5 MB.")
    telegram_file = await message.bot.get_file(item.file_id)
    buffer = BytesIO()
    await message.bot.download(telegram_file, destination=buffer)
    return filename, mime_type, buffer.getvalue(), item.file_unique_id


@router.message(Form.document_file, F.document | F.photo)
async def save_supervisor_document_file(message: Message, state: FSMContext):
    employee = await require_supervisor(message)
    if not employee:
        await state.clear()
        return
    data = await state.get_data()
    try:
        filename, mime_type, content, unique_id = await telegram_document_payload(message)
        if data.get("document_mode") == "update":
            version = await sync_to_async(append_supervisor_document_version)(
                employee.id,
                data["document_id"],
                filename,
                mime_type,
                content,
                file_unique_id=unique_id,
            )
            document = version.document
        else:
            document, version = await sync_to_async(create_supervisor_document)(
                employee.id,
                data["document_workplace_id"],
                data["document_kind"],
                filename,
                mime_type,
                content,
                description=data.get("document_description", ""),
                file_unique_id=unique_id,
            )
    except (KeyError, SupervisorDocumentError) as error:
        await message.answer(t(employee.language, "document_upload_error", error=str(error)))
        return
    await state.clear()
    await message.answer(
        t(
            employee.language,
            "document_saved",
            type=document_kind_label(employee.language, document.kind),
            workplace=document.workplace.name,
            version=version.version_number,
        ),
        reply_markup=menu(employee.language, employee.category),
    )


@router.message(Form.document_file)
async def reject_invalid_supervisor_document(message: Message):
    employee = await require_supervisor(message)
    if employee:
        await message.answer(t(employee.language, "send_valid_document_file"))
