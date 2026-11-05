from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import IntentoInicioSesion


class LoginConIntentosLimitadosForm(AuthenticationForm):
    def _user(self, username):
        user_model = get_user_model()
        return user_model._default_manager.filter(
            **{user_model.USERNAME_FIELD: username},
        ).first()

    def clean(self):
        username = self.cleaned_data.get('username')
        user = self._user(username) if username else None
        if user:
            self._validar_bloqueo(user)

        try:
            cleaned_data = super().clean()
        except ValidationError:
            if user:
                self._procesar_fallo_inicio(user)
            raise

        if user:
            IntentoInicioSesion.objects.filter(usuario=user).delete()
        return cleaned_data

    def _validar_bloqueo(self, user):
        intento = IntentoInicioSesion.objects.filter(usuario=user).first()
        if not intento or not intento.bloqueado_hasta:
            return
        now = timezone.now()
        if intento.bloqueado_hasta > now:
            minutos = max(
                1,
                int((intento.bloqueado_hasta - now).total_seconds() // 60) + 1,
            )
            raise ValidationError(
                f"Inicio de sesión bloqueado temporalmente. "
                f"Inténtalo de nuevo en aproximadamente {minutos} minutos."
            )
        intento.delete()

    def _procesar_fallo_inicio(self, user):
        intento = self._registrar_fallo(user)
        if not intento.bloqueado_hasta:
            return
        minutos = max(1, (settings.LOGIN_LOCKOUT_DURATION + 59) // 60)
        raise ValidationError(
            "Se alcanzó el límite de intentos. "
            f"El inicio de sesión está bloqueado durante {minutos} minutos."
        ) from None

    @transaction.atomic
    def _registrar_fallo(self, user):
        intento, _ = IntentoInicioSesion.objects.select_for_update().get_or_create(
            usuario=user,
        )
        intento.intentos_fallidos += 1
        if intento.intentos_fallidos >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            intento.bloqueado_hasta = timezone.now() + timedelta(
                seconds=settings.LOGIN_LOCKOUT_DURATION,
            )
        intento.save(update_fields=[
            'intentos_fallidos',
            'bloqueado_hasta',
            'actualizado_en',
        ])
        return intento
