from django import forms

from .models import ExportJob


class ExportForm(forms.Form):
    kind = forms.ChoiceField(choices=ExportJob.Kind.choices)
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            raise forms.ValidationError("End date must not precede start date")
        return data
