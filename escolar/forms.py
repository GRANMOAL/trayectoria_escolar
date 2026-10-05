from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm


class InicioSesionForm(AuthenticationForm):
    username = forms.CharField(
        label='Usuario institucional',
        widget=forms.TextInput(attrs={'autofocus': True, 'autocomplete': 'username'}),
    )


class RegistroForm(UserCreationForm):
    username = forms.CharField(
        label='Usuario institucional',
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'username'}),
    )

    class Meta:
        model = get_user_model()
        fields = ('username',)
