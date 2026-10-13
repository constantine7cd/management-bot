import pytest

from apps.accounts.models import User
from apps.organization.models import (
    Employee,
    ScheduleDay,
    ScheduleTemplate,
    Team,
    Workplace,
    WorkplaceShift,
)


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(email="admin@example.com", password="test-pass", role=User.Role.ADMIN, is_staff=True)


@pytest.fixture
def manager(db):
    return User.objects.create_user(email="manager@example.com", password="test-pass", role=User.Role.MANAGER, is_staff=True)


@pytest.fixture
def team(manager):
    team = Team.objects.create(name="Operations")
    team.managers.add(manager)
    return team


@pytest.fixture
def workplace(db):
    return Workplace.objects.create(name="Main office", latitude="47.010500", longitude="28.863800", radius_meters=200, max_accuracy_meters=80)


@pytest.fixture
def workplace_shift(workplace):
    return WorkplaceShift.objects.create(
        workplace=workplace,
        number=1,
        name="Morning",
        start_time="08:00",
        end_time="16:00",
    )


@pytest.fixture
def schedule(db):
    schedule = ScheduleTemplate.objects.create(name="Office schedule")
    for weekday in range(5):
        ScheduleDay.objects.create(schedule=schedule, weekday=weekday, start_time="09:00", end_time="17:00")
    return schedule


@pytest.fixture
def employee(team, workplace, schedule):
    employee = Employee.objects.create(first_name="Ana", last_name="Popescu", email="ana@example.com", team=team, schedule=schedule, hourly_rate="125.50")
    employee.workplaces.add(workplace)
    return employee
