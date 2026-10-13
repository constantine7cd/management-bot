from django.contrib import admin

from .models import LeaveBalance, LeaveRequest, LeaveType

admin.site.register([LeaveType, LeaveBalance, LeaveRequest])
