from django.conf import settings

from .models import Alumno, Consentimiento


PRIVACY_NOTICE_VERSION = getattr(settings, 'PRIVACY_NOTICE_VERSION', '2026-10-26')
PRIVACY_NOTICE_DATE = getattr(settings, 'PRIVACY_NOTICE_DATE', '26 de octubre de 2026')


def consentimiento_requerido():
    return getattr(settings, 'REQUIRE_STUDENT_CONSENT', True)


def alumnos_con_consentimiento():
    alumnos = Alumno.objects.filter(
        bloqueado_arco=False,
        suprimido_arco=False,
    )
    if not consentimiento_requerido():
        return alumnos
    return alumnos.filter(
        consentimientos__estado=Consentimiento.ESTADO_VIGENTE,
        consentimientos__version_aviso=PRIVACY_NOTICE_VERSION,
    ).distinct()
