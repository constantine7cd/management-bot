from django import forms
from django.utils.translation import gettext_lazy as _

from apps.organization.models import Company


class SupervisorDocumentSettingsForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["supervisor_document_edit_days"]
        labels = {"supervisor_document_edit_days": _("Supervisor reupload window (days)")}


class SupervisorDocumentFilterForm(forms.Form):
    kind = forms.ChoiceField(required=False)
    workplace = forms.ChoiceField(required=False)
    supervisor = forms.ChoiceField(required=False)
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, workplaces=(), supervisors=(), **kwargs):
        super().__init__(*args, **kwargs)
        from .models import SupervisorDocument

        self.fields["kind"].choices = [("", _("All types")), *SupervisorDocument.Kind.choices]
        self.fields["workplace"].choices = [("", _("All workplaces")), *[(str(item.pk), item.name) for item in workplaces]]
        self.fields["supervisor"].choices = [("", _("All supervisors")), *[(str(item.pk), str(item)) for item in supervisors]]

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError("The end date cannot be earlier than the start date.")
        return data
