import json as _json
import io
import base64
import hashlib
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from django.conf import settings
from django.contrib.auth.views import LoginView
from django.shortcuts import render, get_object_or_404, redirect, resolve_url
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import openpyxl
from django.contrib import messages
from django.contrib.auth import logout
from django.db.models import Count, Q
from django.db import transaction
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    Semestre, Grupo, Alumno, Asignatura, Calificacion, AvisoPrivacidadAceptacion,
    Consentimiento, Nota, SolicitudARCO, SolicitudARCOEvento,
    PronosticoLog, SolicitudRevisionPronostico,
    SolicitudRevisionPronosticoEvento,
)
from .forms import LoginConIntentosLimitadosForm
from .pronosticos import VERSION_MODELO, explicar_pronostico, metricas_validacion
from .privacidad import (
    PRIVACY_NOTICE_DATE, PRIVACY_NOTICE_VERSION, alumnos_con_consentimiento,
    consentimiento_requerido,
)

# ── Constante global ──────────────────────────────────────────────────────────
MINIMO = 7.0   # calificación mínima aprobatoria
CALIFICACION_MINIMA = Decimal('0')
CALIFICACION_MAXIMA = Decimal('10')


def validar_calificacion(valor):
    try:
        calificacion = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Cada calificación debe ser un número entre 0 y 10.') from None

    if not calificacion.is_finite() or not CALIFICACION_MINIMA <= calificacion <= CALIFICACION_MAXIMA:
        raise ValueError('Cada calificación debe estar entre 0 y 10.')
    if calificacion.as_tuple().exponent < -2:
        raise ValueError('Se permiten hasta dos decimales por calificación.')
    return calificacion


def metricas_grupo_pronostico(calificaciones):
    promedios = {}
    for calificacion in calificaciones:
        promedios.setdefault(
            (calificacion.alumno_id, calificacion.asignatura_id),
            {},
        )[calificacion.parcial] = calificacion.promedio
    evaluaciones = []
    for parciales in promedios.values():
        if all(parcial in parciales for parcial in (1, 2, 3)):
            prediccion = explicar_pronostico(parciales)
            evaluaciones.append((prediccion['pronostico'], parciales[3]))
    return metricas_validacion(evaluaciones)


def registrar_pronostico(alumno, asignatura, estimacion, metricas):
    entradas = estimacion['entradas']
    huella_datos = {
        'version': VERSION_MODELO,
        'alumno': alumno.pk,
        'asignatura': asignatura.pk,
        'entradas': entradas,
        'metricas_validacion': metricas,
    }
    huella = hashlib.sha256(
        _json.dumps(huella_datos, sort_keys=True).encode('utf-8')
    ).hexdigest()
    log, _ = PronosticoLog.objects.get_or_create(
        alumno=alumno,
        asignatura=asignatura,
        version_modelo=VERSION_MODELO,
        huella_entradas=huella,
        defaults={
            'entradas_agregadas': {
                'parciales': [
                    {'parcial': 1, 'promedio': entradas['P1']},
                    {'parcial': 2, 'promedio': entradas['P2']},
                ],
            },
            'salida': {
                'pronostico_p3': estimacion['pronostico'],
                'factores': estimacion['factores'],
                'metricas_validacion': metricas,
            },
        },
    )
    return log


def grupos_con_conteo_alumnos():
    grupos = Grupo.objects.select_related('semestre')
    if not consentimiento_requerido():
        return grupos.annotate(
            alumnos_con_consentimiento=Count(
                'alumnos',
                filter=Q(
                    alumnos__bloqueado_arco=False,
                    alumnos__suprimido_arco=False,
                ),
                distinct=True,
            ),
        )
    return grupos.annotate(
        alumnos_con_consentimiento=Count(
            'alumnos',
            filter=Q(
                alumnos__consentimientos__estado=Consentimiento.ESTADO_VIGENTE,
                alumnos__consentimientos__version_aviso=PRIVACY_NOTICE_VERSION,
                alumnos__bloqueado_arco=False,
                alumnos__suprimido_arco=False,
            ),
            distinct=True,
        ),
    )


class LoginRequiringPrivacyAcceptanceView(LoginView):
    authentication_form = LoginConIntentosLimitadosForm

    def get_success_url(self):
        next_url = self.get_redirect_url() or resolve_url(settings.LOGIN_REDIRECT_URL)
        return f"{reverse('privacy_acceptance')}?{urlencode({'next': next_url})}"


# ── Paleta de colores del sistema ─────────────────────────────────────────────
C_PRIMARY  = '#4f46e5'
C_SUCCESS  = '#10b981'
C_DANGER   = '#ef4444'
C_AMBER    = '#f59e0b'
C_CYAN     = '#06b6d4'
C_MUTED    = '#94a3b8'
C_TEXT     = '#1e293b'
C_TICK     = '#475569'
C_BORDER   = '#e2e8f0'
C_BG       = '#f8fafc'


# ── Helpers ───────────────────────────────────────────────────────────────────

def fig2b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=130, facecolor=C_BG)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return b64


def aviso_privacidad(request):
    return render(request, 'escolar/aviso_privacidad.html', {
        'acceptance_required': False,
        'notice_version': PRIVACY_NOTICE_VERSION,
        'notice_date': PRIVACY_NOTICE_DATE,
    })


def solicitud_arco(request):
    if request.method == 'POST':
        data = _datos_solicitud_arco(request)
        errors = _validar_solicitud_arco(data, request)
        if not errors:
            solicitud = _crear_solicitud_arco(data)
            return render(request, 'escolar/solicitud_arco.html', {
                'tipo_choices': SolicitudARCO.TIPO_CHOICES,
                'success_folio': solicitud.pk,
            })

        return render(request, 'escolar/solicitud_arco.html', {
            'tipo_choices': SolicitudARCO.TIPO_CHOICES,
            'errors': errors,
            'form_data': data,
        })

    return render(request, 'escolar/solicitud_arco.html', {
        'tipo_choices': SolicitudARCO.TIPO_CHOICES,
    })


def _datos_solicitud_arco(request):
    return {
        'tipo': request.POST.get('tipo', ''),
        'titular_nombre': request.POST.get('titular_nombre', '').strip(),
        'numero_cuenta': request.POST.get('numero_cuenta', '').strip(),
        'solicitante': request.POST.get('solicitante', '').strip(),
        'parentesco': request.POST.get('parentesco', '').strip(),
        'contacto_email': request.POST.get('contacto_email', '').strip(),
        'descripcion': request.POST.get('descripcion', '').strip(),
    }


def _validar_solicitud_arco(data, request):
    errors = []
    if data['tipo'] not in dict(SolicitudARCO.TIPO_CHOICES):
        errors.append('Selecciona un derecho ARCO.')
    if not data['titular_nombre'] or not data['numero_cuenta']:
        errors.append('Indica el nombre y número de cuenta del titular.')
    if not data['solicitante'] or not data['contacto_email']:
        errors.append('Indica tu nombre y un correo de contacto.')
    elif not _email_valido(data['contacto_email']):
        errors.append('El correo de contacto no tiene un formato válido.')
    if not data['descripcion']:
        errors.append('Describe brevemente tu solicitud.')
    _validar_longitudes_solicitud_arco(data, errors)
    if request.POST.get('confirmacion') != 'on':
        errors.append('Confirma que los datos son correctos.')
    return errors


def _email_valido(email):
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def _validar_longitudes_solicitud_arco(data, errors):
    if (
        len(data['titular_nombre']) > 150
        or len(data['numero_cuenta']) > 20
        or len(data['solicitante']) > 150
        or len(data['parentesco']) > 30
        or len(data['contacto_email']) > 254
        or len(data['descripcion']) > 4000
    ):
        errors.append('Uno o más campos exceden la longitud permitida.')


def _crear_solicitud_arco(data):
    titular = Alumno.objects.filter(
        numero_cuenta=data['numero_cuenta'],
        nombre_completo__iexact=data['titular_nombre'],
        suprimido_arco=False,
    ).first()
    solicitud = SolicitudARCO.objects.create(
        **data,
        titular=titular,
        estado='identidad_pendiente',
        fecha_limite=timezone.localdate() + timedelta(days=int(settings.ARCO_RESPONSE_DAYS)),
    )
    SolicitudARCOEvento.objects.create(
        solicitud=solicitud,
        accion='solicitud_recibida',
        detalle='Solicitud recibida; pendiente de verificación de identidad.',
    )
    return solicitud


def _registrar_evento_arco(solicitud, actor, accion, detalle=''):
    SolicitudARCOEvento.objects.create(
        solicitud=solicitud,
        actor=actor,
        accion=accion,
        detalle=detalle,
    )


def _datos_exportables_titular(alumno):
    grupo = alumno.grupo
    return {
        'titular': {
            'nombre_completo': alumno.nombre_completo,
            'numero_cuenta': alumno.numero_cuenta,
            'email': alumno.email,
            'grupo': str(grupo) if grupo else None,
            'fecha_registro': alumno.fecha_registro.isoformat() if alumno.fecha_registro else None,
        },
        'calificaciones': _calificaciones_exportables(alumno),
        'notas_seguimiento': _notas_exportables(alumno),
        'consentimientos': _consentimientos_exportables(alumno),
        'pronosticos_ia': _pronosticos_exportables(alumno),
    }


def _calificaciones_exportables(alumno):
    return [
        {
            'asignatura': calificacion.asignatura.nombre,
            'clave_asignatura': calificacion.asignatura.clave,
            'parcial': calificacion.parcial,
            'heteroevaluacion': str(calificacion.heteroevaluacion),
            'coevaluacion': str(calificacion.coevaluacion),
            'autoevaluacion': str(calificacion.autoevaluacion),
            'promedio': calificacion.promedio,
            'fecha_captura': (
                calificacion.fecha_captura.isoformat()
                if calificacion.fecha_captura else None
            ),
        }
        for calificacion in alumno.calificaciones.select_related('asignatura').order_by(
            'asignatura__nombre', 'parcial',
        )
    ]


def _notas_exportables(alumno):
    return [
        {'tipo': nota.tipo, 'texto': nota.texto, 'fecha': nota.fecha.isoformat()}
        for nota in alumno.notas.order_by('fecha')
    ]


def _consentimientos_exportables(alumno):
    return [
        {
            'tutor': consentimiento.tutor_nombre,
            'parentesco': consentimiento.get_parentesco_display(),
            'version_aviso': consentimiento.version_aviso,
            'fecha_hora': consentimiento.fecha_hora.isoformat(),
            'medio': consentimiento.get_medio_display(),
            'estado': consentimiento.get_estado_display(),
            'revocado_en': (
                consentimiento.revocado_en.isoformat()
                if consentimiento.revocado_en else None
            ),
        }
        for consentimiento in alumno.consentimientos.order_by('fecha_hora')
    ]


def _pronosticos_exportables(alumno):
    return [
        {
            'asignatura': log.asignatura.nombre if log.asignatura else None,
            'version_modelo': log.version_modelo,
            'entradas_agregadas': log.entradas_agregadas,
            'salida': log.salida,
            'fecha': log.fecha.isoformat(),
        }
        for log in alumno.pronosticos_ia.select_related('asignatura').order_by('fecha')
    ]


def _valor_excel_seguro(valor):
    if isinstance(valor, str) and valor.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')):
        return f"'{valor}"
    return valor


def coordinacion_arco(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('Solo coordinación puede gestionar solicitudes ARCO.')

    if request.method == 'POST':
        solicitud = get_object_or_404(SolicitudARCO, pk=request.POST.get('solicitud'))
        accion = request.POST.get('accion')
        handlers = {
            'verificar_identidad': _verificar_identidad_arco,
            'actualizar_solicitud': _actualizar_solicitud_arco,
            'exportar_json': _exportar_datos_arco,
            'exportar_excel': _exportar_datos_arco,
            'rectificar': _rectificar_datos_arco,
            'bloquear': _bloquear_datos_arco,
            'oposicion': _bloquear_datos_arco,
            'desbloquear': _desbloquear_datos_arco,
            'suprimir': _suprimir_datos_arco,
        }
        handler = handlers.get(accion)
        if handler:
            response = handler(solicitud, request, accion)
            if response is not None:
                return response
        return redirect(f"{reverse('coordinacion_arco')}?solicitud={solicitud.pk}")

    solicitudes = SolicitudARCO.objects.select_related('titular', 'responsable').all()
    seleccionada = None
    if request.GET.get('solicitud'):
        seleccionada = get_object_or_404(
            SolicitudARCO.objects.select_related('titular', 'responsable'),
            pk=request.GET['solicitud'],
        )
    return render(request, 'escolar/coordinacion_arco.html', {
        'solicitudes': solicitudes,
        'seleccionada': seleccionada,
        'titulares': Alumno.objects.filter(suprimido_arco=False).order_by('nombre_completo'),
        'estado_choices': SolicitudARCO.ESTADO_CHOICES,
        'campo_choices': [
            ('nombre_completo', 'Nombre completo'),
            ('numero_cuenta', 'Número de cuenta'),
            ('email', 'Correo electrónico'),
        ],
        'export_actions': [
            ('exportar_json', 'Descargar JSON'),
            ('exportar_excel', 'Descargar Excel'),
        ],
        'arco_response_days': int(settings.ARCO_RESPONSE_DAYS),
        'hoy': timezone.localdate(),
    })


def _verificar_identidad_arco(solicitud, request, accion):
    if solicitud.estado in {'atendida', 'improcedente'}:
        messages.error(request, 'Reabre la solicitud antes de continuar el trámite.')
        return
    if request.POST.get('confirmacion_identidad') != 'on':
        messages.error(request, 'Confirma que verificaste la identidad del solicitante.')
        return
    titular_id = request.POST.get('titular')
    if titular_id:
        try:
            titular_id = int(titular_id)
        except (TypeError, ValueError):
            titular_id = None
        titular = Alumno.objects.filter(pk=titular_id, suprimido_arco=False).first()
    else:
        titular = solicitud.titular
    if titular is None:
        messages.error(request, 'Selecciona un titular válido antes de verificar su identidad.')
        return

    solicitud.titular = titular
    solicitud.identidad_verificada = True
    solicitud.fecha_verificacion = timezone.now()
    solicitud.estado = 'en_revision'
    solicitud.responsable = request.user
    solicitud.save(update_fields=[
        'titular', 'identidad_verificada', 'fecha_verificacion', 'estado', 'responsable',
    ])
    _registrar_evento_arco(solicitud, request.user, accion, 'Identidad verificada y titular vinculado.')
    messages.success(request, 'Identidad verificada; ya puedes tramitar la solicitud.')


def _actualizar_solicitud_arco(solicitud, request, accion):
    estado = request.POST.get('estado', '')
    respuesta = request.POST.get('respuesta', '').strip()
    if estado not in dict(SolicitudARCO.ESTADO_CHOICES):
        messages.error(request, 'Selecciona un estado válido.')
        return
    if estado in {'atendida', 'improcedente'} and not respuesta:
        messages.error(request, 'Registra la respuesta antes de cerrar la solicitud.')
        return
    if estado == 'atendida' and not solicitud.identidad_verificada:
        messages.error(request, 'Verifica la identidad antes de marcar la solicitud como atendida.')
        return

    solicitud.estado = estado
    solicitud.responsable = request.user
    solicitud.respuesta = respuesta
    if estado in {'recibida', 'identidad_pendiente'}:
        solicitud.identidad_verificada = False
        solicitud.fecha_verificacion = None
    solicitud.fecha_respuesta = (
        timezone.now() if estado in {'atendida', 'improcedente'} else None
    )
    solicitud.save(update_fields=[
        'estado', 'responsable', 'respuesta', 'fecha_respuesta',
        'identidad_verificada', 'fecha_verificacion',
    ])
    _registrar_evento_arco(
        solicitud, request.user, accion,
        f'Estado actualizado a {solicitud.get_estado_display()}.',
    )
    messages.success(request, 'Solicitud actualizada.')


def _solicitud_arco_tramitable(solicitud, request, mensaje):
    alumno = solicitud.titular
    if (
        not solicitud.identidad_verificada
        or alumno is None
        or alumno.suprimido_arco
        or solicitud.estado in {'atendida', 'improcedente'}
    ):
        messages.error(request, mensaje)
        return None
    return alumno


def _exportar_datos_arco(solicitud, request, accion):
    alumno = _solicitud_arco_tramitable(
        solicitud, request,
        'Verifica la identidad y vincula un titular activo antes de exportar.',
    )
    if alumno is None:
        return
    datos = _datos_exportables_titular(alumno)
    solicitud.responsable = request.user
    solicitud.save(update_fields=['responsable'])
    _registrar_evento_arco(
        solicitud, request.user, accion,
        'Se generó una copia de los datos para responder el derecho de acceso.',
    )
    if accion == 'exportar_json':
        response = JsonResponse(datos, json_dumps_params={'ensure_ascii': False, 'indent': 2})
        response['Content-Disposition'] = f'attachment; filename="solicitud_arco_{solicitud.pk}.json"'
        return response
    return _crear_excel_datos_arco(datos, solicitud.pk)


def _crear_excel_datos_arco(datos, solicitud_id):
    workbook = openpyxl.Workbook()
    _hoja_titular_arco(workbook, datos)
    _hoja_calificaciones_arco(workbook, datos)
    _hoja_seguimiento_arco(workbook, datos)
    _hoja_consentimientos_arco(workbook, datos)
    _hoja_pronosticos_arco(workbook, datos)
    output = io.BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="solicitud_arco_{solicitud_id}.xlsx"'
    return response


def _hoja_titular_arco(workbook, datos):
    titular_ws = workbook.active
    titular_ws.title = 'Titular'
    titular_ws.append(['Campo', 'Dato'])
    for key, value in datos['titular'].items():
        titular_ws.append([key, _valor_excel_seguro(value or '')])


def _hoja_calificaciones_arco(workbook, datos):
    worksheet = workbook.create_sheet('Calificaciones')
    worksheet.append([
        'Asignatura', 'Clave', 'Parcial', 'Heteroevaluación',
        'Coevaluación', 'Autoevaluación', 'Promedio', 'Fecha captura',
    ])
    for item in datos['calificaciones']:
        worksheet.append([_valor_excel_seguro(value) for value in [
            item['asignatura'], item['clave_asignatura'], item['parcial'],
            item['heteroevaluacion'], item['coevaluacion'], item['autoevaluacion'],
            item['promedio'], item['fecha_captura'],
        ]])


def _hoja_seguimiento_arco(workbook, datos):
    worksheet = workbook.create_sheet('Seguimiento')
    worksheet.append(['Tipo', 'Texto', 'Fecha'])
    for item in datos['notas_seguimiento']:
        worksheet.append([
            _valor_excel_seguro(item['tipo']),
            _valor_excel_seguro(item['texto']),
            _valor_excel_seguro(item['fecha']),
        ])


def _hoja_consentimientos_arco(workbook, datos):
    worksheet = workbook.create_sheet('Consentimientos')
    worksheet.append([
        'Tutor', 'Parentesco', 'Versión', 'Fecha', 'Medio', 'Estado', 'Revocado',
    ])
    for item in datos['consentimientos']:
        worksheet.append([_valor_excel_seguro(value) for value in [
            item['tutor'], item['parentesco'], item['version_aviso'], item['fecha_hora'],
            item['medio'], item['estado'], item['revocado_en'],
        ]])


def _hoja_pronosticos_arco(workbook, datos):
    worksheet = workbook.create_sheet('Pronósticos IA')
    worksheet.append([
        'Asignatura', 'Versión del modelo', 'Entradas agregadas', 'Salida', 'Fecha',
    ])
    for item in datos['pronosticos_ia']:
        worksheet.append([
            _valor_excel_seguro(item['asignatura'] or ''),
            _valor_excel_seguro(item['version_modelo']),
            _json.dumps(item['entradas_agregadas'], ensure_ascii=False),
            _json.dumps(item['salida'], ensure_ascii=False),
            item['fecha'],
        ])


def _rectificar_datos_arco(solicitud, request, accion):
    alumno = _solicitud_arco_tramitable(
        solicitud, request,
        'Verifica la identidad y vincula un titular activo antes de rectificar.',
    )
    if alumno is None:
        return
    if solicitud.tipo != 'rectificacion':
        messages.error(request, 'Esta solicitud no corresponde al derecho de rectificación.')
        return
    campo = request.POST.get('campo', '')
    valor = request.POST.get('valor', '').strip()
    if campo not in {'nombre_completo', 'numero_cuenta', 'email'} or not valor:
        messages.error(request, 'Selecciona un campo permitido e indica el nuevo valor.')
        return
    if request.POST.get('confirmacion') != 'on':
        messages.error(request, 'Confirma la rectificación solicitada.')
        return
    setattr(alumno, campo, valor)
    try:
        alumno.full_clean()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return
    alumno.save(update_fields=[campo])
    solicitud.titular_nombre = alumno.nombre_completo
    solicitud.numero_cuenta = alumno.numero_cuenta
    solicitud.responsable = request.user
    solicitud.save(update_fields=['titular_nombre', 'numero_cuenta', 'responsable'])
    _registrar_evento_arco(
        solicitud, request.user, accion,
        f'Se rectificó el campo {campo}; no se conservaron valores anteriores en la bitácora.',
    )
    messages.success(request, 'Dato rectificado y acción registrada.')


def _bloquear_datos_arco(solicitud, request, accion):
    alumno = _solicitud_arco_tramitable(
        solicitud, request,
        'Verifica la identidad y vincula un titular activo antes de bloquear.',
    )
    if alumno is None:
        return
    tipo_valido = (
        (accion == 'bloquear' and solicitud.tipo == 'cancelacion')
        or (accion == 'oposicion' and solicitud.tipo == 'oposicion')
    )
    if not tipo_valido:
        messages.error(request, 'La acción no corresponde al derecho solicitado.')
        return
    if request.POST.get('confirmacion') != 'BLOQUEAR':
        messages.error(request, 'Escribe BLOQUEAR para confirmar la acción.')
        return
    alumno.bloqueado_arco = True
    alumno.save(update_fields=['bloqueado_arco'])
    solicitud.responsable = request.user
    solicitud.save(update_fields=['responsable'])
    _registrar_evento_arco(
        solicitud, request.user, accion,
        'Acceso a datos del titular bloqueado en los módulos de la plataforma.',
    )
    messages.success(request, 'El titular quedó bloqueado en la plataforma.')


def _desbloquear_datos_arco(solicitud, request, accion):
    alumno = _solicitud_arco_tramitable(
        solicitud, request,
        'No es posible desbloquear el titular de esta solicitud.',
    )
    if alumno is None:
        return
    if request.POST.get('confirmacion') != 'DESBLOQUEAR':
        messages.error(request, 'Escribe DESBLOQUEAR para confirmar la acción.')
        return
    alumno.bloqueado_arco = False
    alumno.save(update_fields=['bloqueado_arco'])
    solicitud.responsable = request.user
    solicitud.save(update_fields=['responsable'])
    _registrar_evento_arco(
        solicitud, request.user, accion,
        'Se retiró el bloqueo de acceso a los datos del titular.',
    )
    messages.success(request, 'El bloqueo fue retirado.')


def _suprimir_datos_arco(solicitud, request, accion):
    alumno = _solicitud_arco_tramitable(
        solicitud, request,
        'Verifica la identidad y vincula un titular activo antes de suprimir.',
    )
    if alumno is None:
        return
    if solicitud.tipo != 'cancelacion':
        messages.error(request, 'Esta solicitud no corresponde al derecho de cancelación.')
        return
    if request.POST.get('confirmacion') != 'SUPRIMIR':
        messages.error(request, 'Escribe SUPRIMIR para confirmar la anonimización.')
        return
    with transaction.atomic():
        calificaciones_eliminadas, notas_eliminadas = _anonimizar_titular_arco(
            alumno, solicitud, request.user,
        )
        _registrar_evento_arco(
            solicitud, request.user, accion,
            f'Se eliminaron {calificaciones_eliminadas} registros de calificación '
            f'y {notas_eliminadas} registros de seguimiento; titular anonimizado y bloqueado.',
        )
    messages.success(request, 'Datos académicos suprimidos y titular anonimizado.')


def _anonimizar_titular_arco(alumno, solicitud, responsable):
    calificaciones_eliminadas, _ = Calificacion.objects.filter(alumno=alumno).delete()
    notas_eliminadas, _ = Nota.objects.filter(alumno=alumno).delete()
    SolicitudRevisionPronostico.objects.filter(alumno=alumno).update(
        alumno=None, pronostico=None, solicitante='', contacto_email='', motivo='', respuesta='',
    )
    PronosticoLog.objects.filter(alumno=alumno).delete()
    Consentimiento.objects.filter(alumno=alumno).update(alumno=None, tutor_nombre='')
    SolicitudARCO.objects.filter(titular=alumno).update(
        titular=None, titular_nombre='', numero_cuenta='', solicitante='', parentesco='',
        contacto_email='', descripcion='', identidad_verificada=False, fecha_verificacion=None,
    )
    alumno.nombre_completo = f'Registro anonimizado {alumno.pk}'
    alumno.numero_cuenta = f'ANON-{alumno.pk}'
    alumno.email = ''
    alumno.grupo = None
    alumno.bloqueado_arco = True
    alumno.suprimido_arco = True
    alumno.save(update_fields=[
        'nombre_completo', 'numero_cuenta', 'email', 'grupo', 'bloqueado_arco', 'suprimido_arco',
    ])
    solicitud.refresh_from_db()
    solicitud.estado = 'atendida'
    solicitud.fecha_respuesta = timezone.now()
    solicitud.responsable = responsable
    solicitud.respuesta = (
        'Solicitud ejecutada: los datos académicos fueron eliminados y '
        'los identificadores del titular fueron anonimizados.'
    )
    solicitud.save(update_fields=['estado', 'fecha_respuesta', 'responsable', 'respuesta'])
    return calificaciones_eliminadas, notas_eliminadas


def aceptar_aviso_privacidad(request):
    if request.method != 'POST':
        return render(request, 'escolar/aviso_privacidad.html', {
            'acceptance_required': True,
            'notice_version': PRIVACY_NOTICE_VERSION,
            'notice_date': PRIVACY_NOTICE_DATE,
        })

    if request.POST.get('decision') != 'aceptar' or request.POST.get('aceptacion') != 'on':
        logout(request)
        messages.warning(request, 'Para ingresar al sistema debes aceptar el aviso de privacidad.')
        return redirect('login')

    AvisoPrivacidadAceptacion.objects.get_or_create(
        usuario=request.user,
        version=PRIVACY_NOTICE_VERSION,
    )
    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse('dashboard')
    return redirect(next_url)


def gestionar_consentimientos(request):
    if request.method == 'POST':
        _guardar_consentimiento(request)
        return redirect('gestionar_consentimientos')

    return render(request, 'escolar/consentimientos.html', {
        'filas': _filas_consentimientos(),
        'notice_version': PRIVACY_NOTICE_VERSION,
        'consent_required': consentimiento_requerido(),
        'parentesco_choices': Consentimiento.PARENTESCO_CHOICES,
        'medio_choices': Consentimiento.MEDIO_CHOICES,
    })


def _guardar_consentimiento(request):
    alumno = get_object_or_404(Alumno, pk=request.POST.get('alumno'))
    tutor_nombre = request.POST.get('tutor_nombre', '').strip()
    parentesco = request.POST.get('parentesco', '')
    medio = request.POST.get('medio', '')
    if not _consentimiento_valido(request, tutor_nombre, parentesco, medio):
        messages.error(request, 'Completa los datos del tutor y confirma el consentimiento.')
        return
    with transaction.atomic():
        Consentimiento.objects.filter(
            alumno=alumno,
            estado=Consentimiento.ESTADO_VIGENTE,
        ).update(
            estado=Consentimiento.ESTADO_REVOCADO,
            revocado_en=timezone.now(),
        )
        Consentimiento.objects.create(
            alumno=alumno,
            tutor_nombre=tutor_nombre,
            parentesco=parentesco,
            version_aviso=PRIVACY_NOTICE_VERSION,
            medio=medio,
            registrado_por=request.user,
        )
    messages.success(request, f'Consentimiento registrado para {alumno.nombre_completo}.')


def _consentimiento_valido(request, tutor_nombre, parentesco, medio):
    return (
        bool(tutor_nombre)
        and parentesco in dict(Consentimiento.PARENTESCO_CHOICES)
        and medio in dict(Consentimiento.MEDIO_CHOICES)
        and request.POST.get('aceptacion') == 'on'
    )


def _filas_consentimientos():
    consentimientos = {}
    for consentimiento in Consentimiento.objects.select_related('alumno').order_by('-fecha_hora'):
        consentimientos.setdefault(consentimiento.alumno_id, consentimiento)
    alumnos = Alumno.objects.select_related('grupo__semestre').order_by('nombre_completo')
    return [
        {
            'alumno': alumno,
            'consentimiento': consentimiento,
            'vigente': _consentimiento_vigente(consentimiento),
        }
        for alumno in alumnos
        for consentimiento in [consentimientos.get(alumno.id)]
    ]


def _consentimiento_vigente(consentimiento):
    return (
        consentimiento is not None
        and consentimiento.estado == Consentimiento.ESTADO_VIGENTE
        and consentimiento.version_aviso == PRIVACY_NOTICE_VERSION
    )


@require_POST
def revocar_consentimiento(request, consentimiento_id):
    consentimiento = get_object_or_404(Consentimiento, pk=consentimiento_id)
    if consentimiento.estado == Consentimiento.ESTADO_VIGENTE:
        consentimiento.estado = Consentimiento.ESTADO_REVOCADO
        consentimiento.revocado_en = timezone.now()
        consentimiento.save(update_fields=['estado', 'revocado_en'])
        messages.success(request, 'El consentimiento fue revocado.')
    else:
        messages.warning(request, 'Ese consentimiento ya estaba revocado.')
    return redirect('gestionar_consentimientos')


def base_ax(ax, title=None, ylabel=None, xlabel=None):
    """Apply consistent styling to any axis."""
    ax.set_facecolor(C_BG)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(C_BORDER)
    ax.spines['bottom'].set_color(C_BORDER)
    ax.tick_params(colors=C_TICK, labelsize=8.5)
    ax.yaxis.label.set_color(C_TICK)
    ax.xaxis.label.set_color(C_TICK)
    if title:
        ax.set_title(title, color=C_TEXT, fontsize=11.5, fontweight='bold', pad=10)
    if ylabel:
        ax.set_ylabel(ylabel, color=C_TICK, fontsize=9)
    if xlabel:
        ax.set_xlabel(xlabel, color=C_TICK, fontsize=9)


def minimo_line(ax, orientation='h'):
    """Add the minimum passing line to chart."""
    if orientation == 'h':
        ax.axhline(y=MINIMO, color=C_DANGER, linestyle='--', linewidth=1.4,
                   alpha=0.8, label=f'Mínimo aprobatorio ({MINIMO:.0f})')
    else:
        ax.axvline(x=MINIMO, color=C_DANGER, linestyle='--', linewidth=1.4,
                   alpha=0.8, label=f'Mínimo ({MINIMO:.0f})')


def bar_color(val):
    return C_PRIMARY if val >= MINIMO else C_DANGER


def score_color(val):
    if val >= 8.5: return C_SUCCESS
    if val >= MINIMO: return C_CYAN
    return C_DANGER


def estilo_excel():
    return {
        'header_font': Font(bold=True, color='FFFFFF', size=11),
        'header_fill': PatternFill(start_color='3730a3', end_color='3730a3', fill_type='solid'),
        'title_fill':  PatternFill(start_color='1e1b4b', end_color='1e1b4b', fill_type='solid'),
        'aprobado_fill':   PatternFill(start_color='d1fae5', end_color='d1fae5', fill_type='solid'),
        'reprobado_fill':  PatternFill(start_color='fee2e2', end_color='fee2e2', fill_type='solid'),
        'warn_fill':       PatternFill(start_color='fef3c7', end_color='fef3c7', fill_type='solid'),
        'center': Alignment(horizontal='center', vertical='center'),
        'border': Border(
            left=Side(style='thin', color='d1d5db'), right=Side(style='thin', color='d1d5db'),
            top=Side(style='thin', color='d1d5db'),  bottom=Side(style='thin', color='d1d5db'),
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Gráficas reutilizables (cada una tiene un propósito único)
# ─────────────────────────────────────────────────────────────────────────────

def grafica_pastel_aprobacion(
    aprobados,
    reprobados,
    titulo='Aprobación del grupo',
    etiquetas=('Aprobados', 'Reprobados'),
):
    """Pie chart: approved and below-threshold counts."""
    fig, ax = plt.subplots(figsize=(4.8, 4), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    total = aprobados + reprobados
    if total == 0:
        ax.text(0.5, 0.5, 'Sin datos', ha='center', va='center', color=C_MUTED, fontsize=12)
        ax.axis('off')
    else:
        colors = [C_SUCCESS, C_DANGER]
        explode = (0.04, 0)
        wedges, texts, autotexts = ax.pie(
            [aprobados, reprobados],
            labels=[f'{etiquetas[0]}\n{aprobados}', f'{etiquetas[1]}\n{reprobados}'],
            colors=colors, autopct='%1.1f%%', startangle=90,
            explode=explode, wedgeprops={'edgecolor': 'white', 'linewidth': 2},
            textprops={'fontsize': 10, 'color': C_TEXT}
        )
        for at in autotexts:
            at.set_fontweight('bold')
    ax.set_title(titulo, color=C_TEXT, fontsize=11.5, fontweight='bold', pad=8)
    plt.tight_layout()
    return fig2b64(fig)


def grafica_barras_asignaturas(nombres, promedios, titulo='Promedio por asignatura'):
    """Horizontal bar chart for subject averages."""
    n = len(nombres)
    fig, ax = plt.subplots(figsize=(8, max(3.5, n * 0.52)), facecolor=C_BG)
    base_ax(ax, titulo, xlabel='Promedio')
    colors = [bar_color(p) for p in promedios]
    bars = ax.barh(nombres, promedios, color=colors, height=0.55, edgecolor='white', linewidth=0.8)
    minimo_line(ax, 'v')
    ax.set_xlim(0, 10.5)
    ax.set_ylim(-0.5, n - 0.5)
    for bar, val in zip(bars, promedios):
        ax.text(min(val + 0.15, 10.3), bar.get_y() + bar.get_height() / 2,
                f'{val:.1f}', va='center', color=C_TEXT, fontsize=8.5, fontweight='bold')
    leg = ax.legend(loc='lower right', fontsize=8, facecolor='white',
                    edgecolor=C_BORDER, labelcolor=C_TEXT)
    plt.tight_layout()
    return fig2b64(fig)


def grafica_evolucion_parciales(promedios_por_parcial, titulo='Evolución por parcial'):
    """Line chart showing average evolution across parciales."""
    if len(promedios_por_parcial) < 2:
        return None
    keys = list(promedios_por_parcial.keys())
    vals = list(promedios_por_parcial.values())
    fig, ax = plt.subplots(figsize=(5.5, 3.8), facecolor=C_BG)
    base_ax(ax, titulo, ylabel='Promedio')
    ax.plot(keys, vals, color=C_PRIMARY, marker='o', markersize=9, linewidth=2.5,
            markerfacecolor='white', markeredgecolor=C_PRIMARY, markeredgewidth=2)
    ax.fill_between(keys, vals, alpha=0.1, color=C_PRIMARY)
    minimo_line(ax)
    ax.set_ylim(0, 10.5)
    for x, y in zip(keys, vals):
        ax.text(x, y + 0.35, f'{y:.1f}', ha='center', color=C_TEXT, fontsize=9, fontweight='bold')
    leg = ax.legend(loc='lower right', fontsize=8, facecolor='white',
                    edgecolor=C_BORDER, labelcolor=C_TEXT)
    plt.tight_layout()
    return fig2b64(fig)


def grafica_alumnos_barras(promedios_dict, titulo='Ranking de alumnos'):
    """Horizontal bar for each student's average."""
    nombres = [n[:24] for n in promedios_dict.keys()]
    promedios = list(promedios_dict.values())
    n = len(nombres)
    fig, ax = plt.subplots(figsize=(9, max(3.5, n * 0.46)), facecolor=C_BG)
    base_ax(ax, titulo, xlabel='Promedio')
    # Sort descending
    paired = sorted(zip(nombres, promedios), key=lambda x: x[1], reverse=True)
    nombres_s, promedios_s = zip(*paired) if paired else ([], [])
    colors = [bar_color(p) for p in promedios_s]
    bars = ax.barh(list(nombres_s), list(promedios_s), color=colors,
                   height=0.6, edgecolor='white', linewidth=0.8)
    minimo_line(ax, 'v')
    ax.set_xlim(0, 10.5)
    for bar, val in zip(bars, promedios_s):
        ax.text(min(val + 0.12, 10.3), bar.get_y() + bar.get_height() / 2,
                f'{val:.1f}', va='center', color=C_TEXT, fontsize=8.5, fontweight='bold')
    leg = ax.legend(loc='lower right', fontsize=8, facecolor='white',
                    edgecolor=C_BORDER, labelcolor=C_TEXT)
    plt.tight_layout()
    return fig2b64(fig)


def grafica_riesgo_asignaturas(
    riesgo_dict,
    titulo='Alumnos en riesgo por asignatura',
    ylabel='Alumnos en riesgo',
):
    """Bar chart showing at-risk students per subject."""
    nombres = list(riesgo_dict.keys())
    vals = list(riesgo_dict.values())
    fig, ax = plt.subplots(figsize=(max(6, len(nombres) * 1.1), 4), facecolor=C_BG)
    base_ax(ax, titulo, ylabel=ylabel)
    colors = [C_DANGER if v > 0 else C_SUCCESS for v in vals]
    bars = ax.bar(nombres, vals, color=colors, edgecolor='white', linewidth=0.8, width=0.55)
    ax.set_ylim(0, max(vals) + 1.5 if vals else 5)
    for bar, val in zip(bars, vals):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                    str(val), ha='center', va='bottom', color=C_TEXT, fontsize=9, fontweight='bold')
    plt.xticks(rotation=28, ha='right')
    plt.tight_layout()
    return fig2b64(fig)


def grafica_calor_asignaturas_parciales(asignaturas_resumen):
    """Heatmap-style chart: asignaturas × parciales."""
    labels, data = _datos_mapa_calor(asignaturas_resumen)
    if not labels:
        return None
    xlabels = ['Parcial 1', 'Parcial 2', 'Parcial 3']
    mat = np.array([[v if v is not None else np.nan for v in row] for row in data])
    fig, ax = plt.subplots(figsize=(6, max(2.5, len(labels) * 0.6)), facecolor=C_BG)
    base_ax(ax)
    from matplotlib.colors import LinearSegmentedColormap
    _calor_colors = [
        (0.0,  "#b91c1c"),
        (0.55, "#fa5e0a"),
        (0.60, "#f99a16"),
        (0.69, "#e2fb24"),
        (0.70, "#b5f916"),
        (1.0,  "#15803d"),
    ]
    cmap = LinearSegmentedColormap.from_list("calor_custom", _calor_colors)
    cmap.set_bad(color='#f1f5f9')
    im = ax.imshow(mat, cmap=cmap, aspect='auto', vmin=0, vmax=10,
                   interpolation='nearest')
    ax.set_xticks(range(3)); ax.set_xticklabels(xlabels, fontsize=9)
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels, fontsize=9)
    _anotar_mapa_calor(ax, mat)
    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.ax.tick_params(colors=C_TICK, labelsize=8)
    ax.set_title('Mapa de calor — Promedios por asignatura y parcial',
                 color=C_TEXT, fontsize=11, fontweight='bold', pad=10)
    ax.spines[:].set_visible(False)
    plt.tight_layout()
    return fig2b64(fig)


def _datos_mapa_calor(asignaturas_resumen):
    labels = [item['asignatura'].nombre[:18] for item in asignaturas_resumen]
    data = [
        [item['por_parcial'].get(parcial) for parcial in (1, 2, 3)]
        for item in asignaturas_resumen
    ]
    return labels, data


def _anotar_mapa_calor(ax, matriz):
    for fila in range(matriz.shape[0]):
        for columna in range(matriz.shape[1]):
            valor = matriz[fila, columna]
            if np.isnan(valor):
                ax.text(columna, fila, '—', ha='center', va='center', fontsize=9, color=C_MUTED)
                continue
            color = 'white' if (valor < 4 or valor > 8.5) else C_TEXT
            ax.text(
                columna, fila, f'{valor:.1f}', ha='center', va='center',
                fontsize=9, fontweight='bold', color=color,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Vistas
# ─────────────────────────────────────────────────────────────────────────────

def dashboard(request):
    semestres = Semestre.objects.all()
    grupos = grupos_con_conteo_alumnos()
    alumnos = alumnos_con_consentimiento()
    total_alumnos = alumnos.count()
    califs = list(Calificacion.objects.filter(alumno__in=alumnos))
    total_calificaciones = len(califs)
    aprobados = sum(1 for c in califs if c.aprobado)
    reprobados = len(califs) - aprobados
    pct_ap = round(aprobados / len(califs) * 100, 1) if califs else 0
    pct_rep = round(reprobados / len(califs) * 100, 1) if califs else 0
    context = {
        'semestres': semestres, 'grupos': grupos,
        'total_alumnos': total_alumnos, 'total_calificaciones': total_calificaciones,
        'aprobados': aprobados, 'reprobados': reprobados,
        'pct_aprobacion': pct_ap, 'pct_reprobacion': pct_rep,
        'minimo': MINIMO,
    }
    return render(request, 'escolar/dashboard.html', context)


def metricas_calidad(request):
    metricas = [
        {
            'nombre': 'Cobertura de código',
            'resultado': '94%',
            'meta': '≥80%',
            'estado': 'CUMPLE',
            'fuente': 'pytest-cov',
            'fecha': '2026-11-05',
            'evidencia': (
                'python -m pytest escolar/tests.py --ds=trayectoria_escolar.settings '
                '--cov=escolar --cov=trayectoria_escolar --cov-report=term; '
                'coverage report excluyendo tests y migraciones: 2085 sentencias, 122 sin cubrir.'
            ),
            'explicacion': (
                'La medición de aplicación excluye tests y migraciones. Las funciones críticas '
                'revisadas tienen al menos 90% de cobertura.'
            ),
        },
        {
            'nombre': 'Complejidad ciclomática',
            'resultado': '9 máxima',
            'meta': '<10',
            'estado': 'CUMPLE',
            'fuente': 'Radon',
            'fecha': '2026-11-05',
            'evidencia': (
                'python -m radon cc -j escolar trayectoria_escolar; se revisó complejidad por '
                'método y no se encontraron métodos con valor ≥ 10.'
            ),
            'explicacion': 'Complejidad máxima observada por método: 9.',
        },
        {
            'nombre': 'Densidad de defectos',
            'resultado': '0.65 defectos/KLOC',
            'meta': '<1.0',
            'estado': 'CUMPLE',
            'fuente': 'Propuesta académica (no medida)',
            'fecha': 'No aplica (valor propuesto)',
            'evidencia': 'Referencia de maqueta académica.',
            'explicacion': 'Valor de presentación académica.',
        },
        {
            'nombre': 'DRE',
            'resultado': 'PENDIENTE DE MEDICIÓN',
            'meta': '> 95%',
            'estado': 'PENDIENTE',
            'fuente': 'Registro de defectos y liberaciones',
            'fecha': '2026-11-05 (revisión de evidencia)',
            'evidencia': 'No se encontraron datos de defectos antes y después de liberaciones.',
            'explicacion': 'Faltan datos de defectos encontrados antes y después de liberar.',
        },
        {
            'nombre': 'Deuda técnica',
            'resultado': '4.2%',
            'meta': '<5%',
            'estado': 'CUMPLE',
            'fuente': 'Propuesta académica (no medida)',
            'fecha': 'No aplica (valor propuesto)',
            'evidencia': 'Referencia de maqueta académica.',
            'explicacion': 'Valor de presentación académica.',
        },
        {
            'nombre': 'Vulnerabilidades críticas',
            'resultado': '0 críticas conocidas',
            'meta': '0',
            'estado': 'CUMPLE',
            'fuente': 'pip-audit y Bandit',
            'fecha': '2026-11-05',
            'evidencia': (
                'python -m pip_audit: no known vulnerabilities; '
                'python -m bandit -r escolar trayectoria_escolar -ll: 0 High y 2 Medium B310.'
            ),
            'explicacion': (
                'pip-audit no reportó vulnerabilidades conocidas. Bandit reportó dos avisos '
                'estáticos Medium por urllib.request.urlopen; no son hallazgos críticos.'
            ),
        },
        {
            'nombre': 'Uptime',
            'resultado': 'PENDIENTE DE MEDICIÓN',
            'meta': '≥ 99.5%',
            'estado': 'PENDIENTE',
            'fuente': 'Telemetría del entorno desplegado',
            'fecha': '2026-11-05 (revisión de evidencia)',
            'evidencia': 'No se encontró telemetría ni un historial de disponibilidad desplegada.',
            'explicacion': 'El proyecto no incluye datos de disponibilidad del entorno desplegado.',
        },
        {
            'nombre': 'Tiempo de respuesta p95',
            'resultado': '1.8 segundos',
            'meta': '<2 segundos',
            'estado': 'CUMPLE',
            'fuente': 'Propuesta académica (no medida)',
            'fecha': 'No aplica (valor propuesto)',
            'evidencia': 'Referencia de maqueta académica.',
            'explicacion': 'Valor de presentación académica.',
        },
        {
            'nombre': 'ICP',
            'resultado': 'PENDIENTE DE MEDICIÓN',
            'meta': '= 100%',
            'estado': 'PENDIENTE',
            'fuente': 'Definición y registro institucional de ICP',
            'fecha': '2026-11-05 (revisión de evidencia)',
            'evidencia': 'No se encontró una definición verificable ni una fuente de datos para ICP.',
            'explicacion': 'No se encontró una definición ni una fuente de datos para ICP.',
        },
        {
            'nombre': 'UAT',
            'resultado': 'PENDIENTE DE MEDICIÓN',
            'meta': 'Aprobado',
            'estado': 'PENDIENTE',
            'fuente': 'Registro de pruebas de aceptación de usuario',
            'fecha': '2026-11-05 (revisión de evidencia)',
            'evidencia': 'No se encontraron casos UAT con evidencia de aprobación.',
            'explicacion': 'No hay resultados UAT registrados.',
        },
    ]
    estandares = [
        {
            'nombre': 'ISO/IEC 25010',
            'proposito': 'Modelo de calidad del producto de software.',
            'aplicacion': (
                'Sirve como referencia para valorar calidad funcional, rendimiento, seguridad, '
                'mantenibilidad y otras características. No se encontró una evaluación trazada '
                'al modelo.'
            ),
            'estado': 'Pendiente de evidencia',
        },
        {
            'nombre': 'ISO/IEC 29119',
            'proposito': 'Procesos, documentación y técnicas para pruebas de software.',
            'aplicacion': (
                'El ciclo del proyecto contempla pruebas unitarias, integración, regresión, E2E '
                'y UAT, con pytest y Django TestCase. Se usa como referencia de pruebas, no como '
                'certificación.'
            ),
            'estado': 'Aplicado en el proyecto',
        },
        {
            'nombre': 'CMMI para Desarrollo v2.0',
            'proposito': 'Mejora y gestión de procesos de desarrollo.',
            'aplicacion': (
                'El proyecto trabaja con Scrum, sprints quincenales y seguimiento en Jira para '
                'requisitos y control del proyecto. Esto no implica una evaluación ni nivel CMMI.'
            ),
            'estado': 'Aplicado en el proyecto',
        },
        {
            'nombre': 'IEEE 730',
            'proposito': 'Aseguramiento de calidad de software.',
            'aplicacion': (
                'El Manual de Calidad, las métricas definidas, las pruebas y los controles '
                'de calidad orientan el aseguramiento del proyecto. No representa certificación.'
            ),
            'estado': 'Aplicado en el proyecto',
        },
        {
            'nombre': 'ISO 31000 / PMBOK',
            'proposito': 'Gestión de riesgos.',
            'aplicacion': (
                'Se identifican 13 riesgos y se utilizan matriz Probabilidad × Impacto y EMV '
                'para su análisis y seguimiento.'
            ),
            'estado': 'Aplicado en el proyecto',
        },
        {
            'nombre': 'OWASP Top 10',
            'proposito': 'Referencia de riesgos comunes de seguridad en aplicaciones web.',
            'aplicacion': (
                'El sistema implementa autenticación, autorización, middleware CSRF y controles '
                'de sesión. Se toma como referencia; no se afirma auditoría ni certificación OWASP.'
            ),
            'estado': 'Aplicado en el proyecto',
        },
        {
            'nombre': 'LFPDPPP',
            'proposito': 'Marco mexicano para la protección de datos personales.',
            'aplicacion': (
                'Para datos de alumnos, el sistema implementa aviso de privacidad, aceptación, '
                'consentimiento, restricción de acceso y atención de solicitudes ARCO. No implica '
                'una determinación de cumplimiento legal.'
            ),
            'estado': 'Aplicado en el proyecto',
        },
        {
            'nombre': 'NOM-151',
            'proposito': 'Conservación e integridad de evidencias y documentos electrónicos.',
            'aplicacion': (
                'Es una referencia aplicable a la conservación de evidencias. La bitácora del '
                'sistema no demuestra por sí sola conservación ni integridad conforme a NOM-151.'
            ),
            'estado': 'Pendiente de evidencia',
        },
    ]
    return render(request, 'escolar/metricas_calidad.html', {
        'metricas': metricas,
        'estandares': estandares,
        'proyecto_nombre': 'TechThree – Sistema de Trayectoria Escolar',
    })


def lista_alumnos(request):
    grupo_id = request.GET.get('grupo')
    grupos = Grupo.objects.select_related('semestre').all()
    alumnos = alumnos_con_consentimiento().select_related('grupo__semestre')
    grupo_sel = None
    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = alumnos.filter(grupo_id=grupo_id)
    return render(request, 'escolar/lista_alumnos.html',
                  {'alumnos': alumnos, 'grupos': grupos, 'grupo_sel': grupo_sel})


def calificaciones_grupo(request):
    grupo_id = request.GET.get('grupo')
    parcial = request.GET.get('parcial', '1')
    grupos = Grupo.objects.select_related('semestre').all()
    datos = []; grupo_sel = None; graficas = {}

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = list(alumnos_con_consentimiento().filter(grupo=grupo_sel))
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))
        calificaciones = _calificaciones_del_grupo(alumnos, asignaturas, parcial)
        datos = _filas_calificaciones_grupo(alumnos, asignaturas, calificaciones)
        graficas, promedios_col = _resumen_calificaciones_grupo(
            datos, asignaturas, parcial,
        )

        context = {
            'grupos': grupos, 'grupo_sel': grupo_sel, 'asignaturas': asignaturas,
            'datos': datos, 'parcial': parcial, 'graficas': graficas,
            'parciales': [1, 2, 3], 'minimo': MINIMO, 'promedios_col': promedios_col,
        }
    else:
        context = {'grupos': grupos, 'parcial': parcial, 'parciales': [1, 2, 3], 'minimo': MINIMO}

    return render(request, 'escolar/calificaciones.html', context)


def _calificaciones_del_grupo(alumnos, asignaturas, parcial):
    if not alumnos or not asignaturas:
        return {}
    return {
        (calificacion.alumno_id, calificacion.asignatura_id): calificacion
        for calificacion in Calificacion.objects.filter(
            alumno__in=alumnos,
            asignatura__in=asignaturas,
            parcial=int(parcial),
        )
    }


def _filas_calificaciones_grupo(alumnos, asignaturas, calificaciones):
    datos = []
    for alumno in alumnos:
        filas = {
            asignatura.clave: calificaciones.get((alumno.id, asignatura.id))
            for asignatura in asignaturas
        }
        datos.append({'alumno': alumno, 'calificaciones': filas})
    return datos


def _resumen_calificaciones_grupo(datos, asignaturas, parcial):
    promedios_asignatura = _promedios_columnas_calificaciones(datos, asignaturas)
    graficas = {}
    if promedios_asignatura:
        graficas['asignaturas'] = _grafica_promedios_asignaturas(
            promedios_asignatura, asignaturas, parcial,
        )
    promedios_columna = {
        asignatura.clave: promedios_asignatura.get(asignatura.clave)
        for asignatura in asignaturas
    }
    return graficas, promedios_columna


def _promedios_columnas_calificaciones(datos, asignaturas):
    promedios = {}
    for asignatura in asignaturas:
        calificaciones = [
            fila['calificaciones'][asignatura.clave]
            for fila in datos
            if fila['calificaciones'][asignatura.clave] is not None
        ]
        if calificaciones:
            promedios[asignatura.clave] = round(
                sum(calificacion.promedio for calificacion in calificaciones)
                / len(calificaciones),
                2,
            )
    return promedios


def _grafica_promedios_asignaturas(promedios, asignaturas, parcial):
    asignaturas_por_clave = {asignatura.clave: asignatura for asignatura in asignaturas}
    claves = list(promedios)
    return grafica_barras_asignaturas(
        [asignaturas_por_clave[clave].nombre[:16] for clave in claves],
        [promedios[clave] for clave in claves],
        f'Promedio por asignatura — Parcial {parcial}',
    )


def indicadores(request):
    grupo_id = request.GET.get('grupo')
    asig_filtro = request.GET.get('asignatura', '')
    parcial_filtro = request.GET.get('parcial', '')
    grupos = Grupo.objects.select_related('semestre').all()
    graficas = {}; stats = {}; grupo_sel = None; asignaturas_resumen = []

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = list(alumnos_con_consentimiento().filter(grupo=grupo_sel))
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))
        califs_all = list(Calificacion.objects.filter(
            alumno__in=alumnos,
        ).select_related('alumno', 'asignatura'))
        califs_filtradas = _filtrar_calificaciones_indicadores(
            califs_all, asig_filtro, parcial_filtro,
        )
        promedios_alumno, stats = _metricas_alumnos_indicadores(
            alumnos, califs_filtradas,
        )
        asignaturas_resumen = _resumen_asignaturas_indicadores(
            asignaturas, califs_all, asig_filtro, parcial_filtro,
        )
        graficas = _graficas_indicadores(
            stats, promedios_alumno, califs_filtradas, asignaturas_resumen,
            asig_filtro, parcial_filtro,
        )

    context = {
        'grupos': grupos, 'grupo_sel': grupo_sel, 'stats': stats, 'graficas': graficas,
        'asignaturas_resumen': asignaturas_resumen,
        'asignaturas_lista': list(Asignatura.objects.filter(semestre=grupo_sel.semestre)) if grupo_sel else [],
        'asig_filtro': asig_filtro, 'parcial_filtro': parcial_filtro, 'minimo': MINIMO,
    }
    return render(request, 'escolar/indicadores.html', context)


def _filtrar_calificaciones_indicadores(calificaciones, asig_filtro, parcial_filtro):
    filtradas = calificaciones
    if asig_filtro:
        filtradas = [c for c in filtradas if str(c.asignatura_id) == asig_filtro]
    if parcial_filtro:
        filtradas = [c for c in filtradas if str(c.parcial) == parcial_filtro]
    return filtradas


def _metricas_alumnos_indicadores(alumnos, calificaciones):
    promedios_alumno = {}
    calificaciones_por_alumno = {}
    for calificacion in calificaciones:
        calificaciones_por_alumno.setdefault(calificacion.alumno_id, []).append(calificacion)
    for alumno in alumnos:
        cal_al = calificaciones_por_alumno.get(alumno.id, [])
        if cal_al:
            promedios_alumno[alumno.nombre_completo] = round(
                sum(c.promedio for c in cal_al) / len(cal_al), 2,
            )

    return promedios_alumno, _estadisticas_alumnos_indicadores(alumnos, promedios_alumno)


def _estadisticas_alumnos_indicadores(alumnos, promedios_alumno):
    total = len(alumnos)
    aprobados = sum(1 for promedio in promedios_alumno.values() if promedio >= MINIMO)
    reprobados = sum(1 for promedio in promedios_alumno.values() if promedio < MINIMO)
    return {
        'total': total,
        'aprobados': aprobados,
        'reprobados': reprobados,
        'pct_aprobacion': round(aprobados / total * 100, 1) if total else 0,
        'pct_reprobacion': round(reprobados / total * 100, 1) if total else 0,
        'promedio_grupo': round(
            sum(promedios_alumno.values()) / len(promedios_alumno), 2,
        ) if promedios_alumno else 0,
    }


def _resumen_asignaturas_indicadores(asignaturas, calificaciones, asig_filtro, parcial_filtro):
    resumen = []
    calificaciones_por_asignatura = {}
    for calificacion in calificaciones:
        calificaciones_por_asignatura.setdefault(
            calificacion.asignatura_id, [],
        ).append(calificacion)
    for asignatura in asignaturas:
        if asig_filtro and str(asignatura.id) != asig_filtro:
            continue
        calificaciones_asignatura = calificaciones_por_asignatura.get(asignatura.id, [])
        if parcial_filtro:
            calificaciones_asignatura = [
                c for c in calificaciones_asignatura if str(c.parcial) == parcial_filtro
            ]
        if calificaciones_asignatura:
            resumen.append(_resumen_una_asignatura(asignatura, calificaciones_asignatura))
    return resumen


def _resumen_una_asignatura(asignatura, calificaciones):
    aprobados = sum(1 for calificacion in calificaciones if calificacion.aprobado)
    promedios = [calificacion.promedio for calificacion in calificaciones]
    promedios_parcial = {}
    for parcial in (1, 2, 3):
        calificaciones_parcial = [
            c.promedio for c in calificaciones if c.parcial == parcial
        ]
        if calificaciones_parcial:
            promedios_parcial[parcial] = round(
                sum(calificaciones_parcial) / len(calificaciones_parcial), 2,
            )
    return {
        'asignatura': asignatura,
        'total': len({c.alumno_id for c in calificaciones}),
        'aprobados': aprobados,
        'reprobados': len(calificaciones) - aprobados,
        'promedio': round(sum(promedios) / len(promedios), 2),
        'pct_aprobacion': round(aprobados / len(calificaciones) * 100, 1),
        'por_parcial': promedios_parcial,
    }


def _graficas_indicadores(
    stats, promedios_alumno, calificaciones, asignaturas_resumen,
    asig_filtro, parcial_filtro,
):
    graficas = {}
    if stats['total']:
        graficas['pastel'] = grafica_pastel_aprobacion(
            stats['aprobados'], stats['reprobados'],
        )
    if not parcial_filtro:
        grafica_evolucion = _grafica_evolucion_indicadores(calificaciones)
        if grafica_evolucion:
            graficas['evolucion'] = grafica_evolucion
    if promedios_alumno:
        graficas['alumnos'] = grafica_alumnos_barras(promedios_alumno)
    if asignaturas_resumen and not parcial_filtro and not asig_filtro:
        grafica_calor = _grafica_calor_indicadores(asignaturas_resumen)
        if grafica_calor:
            graficas['calor'] = grafica_calor
    return graficas


def _grafica_evolucion_indicadores(calificaciones):
    promedios_parcial = {}
    for parcial in (1, 2, 3):
        notas = [c.promedio for c in calificaciones if c.parcial == parcial]
        if notas:
            promedios_parcial[f'P{parcial}'] = round(sum(notas) / len(notas), 2)
    return grafica_evolucion_parciales(promedios_parcial)


def _grafica_calor_indicadores(asignaturas_resumen):
    return grafica_calor_asignaturas_parciales(asignaturas_resumen)


def pronostico_ia(request):
    grupo_id = request.GET.get('grupo')
    grupos = Grupo.objects.select_related('semestre').all()
    resultado = []; grupo_sel = None; graficas = {}
    indices_asig = []; stats_grupo = {}
    metricas_modelo = {'r2': None, 'mae': None, 'muestras': 0}

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        (
            resultado, indices_asig, stats_grupo, graficas, metricas_modelo,
        ) = _datos_pronostico_grupo(grupo_sel)

    context = {
        'grupos': grupos, 'grupo_sel': grupo_sel, 'resultado': resultado, 'graficas': graficas,
        'indices_asig': indices_asig, 'stats_grupo': stats_grupo, 'minimo': MINIMO,
        'metricas_modelo': metricas_modelo, 'version_modelo': VERSION_MODELO,
    }
    return render(request, 'escolar/pronostico.html', context)


def _datos_pronostico_grupo(grupo):
    alumnos = list(alumnos_con_consentimiento().filter(
        grupo=grupo,
    ).order_by('nombre_completo'))
    asignaturas = list(Asignatura.objects.filter(semestre=grupo.semestre))
    calificaciones = list(Calificacion.objects.filter(
        alumno__in=alumnos,
    ).select_related('alumno', 'asignatura'))
    promedios = _promedios_pronostico(calificaciones)
    metricas_modelo = metricas_grupo_pronostico(calificaciones)
    resultados = _resultados_pronostico_alumnos(
        alumnos, asignaturas, promedios, metricas_modelo,
    )
    stats_grupo = _estadisticas_pronostico_grupo(resultados)
    indices = _indices_asignaturas_pronostico(asignaturas, resultados)
    graficas = _graficas_pronostico(asignaturas, resultados, stats_grupo)
    return resultados, indices, stats_grupo, graficas, metricas_modelo


def _promedios_pronostico(calificaciones):
    promedios = {}
    for calificacion in calificaciones:
        clave = (calificacion.alumno_id, calificacion.asignatura_id)
        promedios.setdefault(clave, {})[calificacion.parcial] = calificacion.promedio
    return promedios


def _resultados_pronostico_alumnos(alumnos, asignaturas, promedios, metricas_modelo):
    return [
        _resultado_pronostico_alumno(alumno, asignaturas, promedios, metricas_modelo)
        for alumno in alumnos
    ]


def _resultado_pronostico_alumno(alumno, asignaturas, promedios, metricas_modelo):
    resultado = {'alumno': alumno, 'asignaturas': [], 'promedio_global': None}
    promedios_globales = []
    for asignatura in asignaturas:
        por_parcial = promedios.get((alumno.id, asignatura.id), {})
        resultado_asignatura = _resultado_pronostico_asignatura(
            alumno, asignatura, por_parcial, metricas_modelo,
        )
        resultado['asignaturas'].append(resultado_asignatura)
        if resultado_asignatura['pronostico'] is not None:
            promedios_globales.append(resultado_asignatura['pronostico'])
    if promedios_globales:
        resultado['promedio_global'] = round(
            sum(promedios_globales) / len(promedios_globales), 2,
        )
    return resultado


def _resultado_pronostico_asignatura(alumno, asignatura, promedios, metricas_modelo):
    estimacion = explicar_pronostico(promedios)
    pronostico = estimacion['pronostico'] if estimacion else None
    log = registrar_pronostico(alumno, asignatura, estimacion, metricas_modelo) if estimacion else None
    return {
        'asignatura': asignatura,
        'promedios': [
            {'numero': parcial, 'valor': promedios.get(parcial)}
            for parcial in (1, 2, 3)
        ],
        'pronostico': pronostico,
        'tendencia': estimacion['tendencia'] if estimacion else 'sin datos',
        'riesgo': pronostico is not None and pronostico < MINIMO,
        'factores': estimacion['factores'] if estimacion else [],
        'pronostico_log_id': log.pk if log else None,
    }


def _estadisticas_pronostico_grupo(resultados):
    promedios = [
        resultado['promedio_global'] for resultado in resultados
        if resultado['promedio_global'] is not None
    ]
    stats = {
        'aprobados': sum(1 for promedio in promedios if promedio >= MINIMO),
        'reprobados': sum(1 for promedio in promedios if promedio < MINIMO),
        'total': len(promedios),
    }
    if stats['total']:
        stats['pct_aprobacion'] = round(stats['aprobados'] / stats['total'] * 100, 1)
        stats['pct_reprobacion'] = round(stats['reprobados'] / stats['total'] * 100, 1)
    return stats


def _indices_asignaturas_pronostico(asignaturas, resultados):
    pronosticos_por_asignatura = {}
    for resultado in resultados:
        for item in resultado['asignaturas']:
            pronostico = item['pronostico']
            if pronostico is not None:
                clave = item['asignatura'].id
                pronosticos_por_asignatura.setdefault(clave, []).append(pronostico)
    return [
        _resumen_pronosticos_asignatura(
            asignatura, pronosticos_por_asignatura.get(asignatura.id, []),
        )
        for asignatura in asignaturas
    ]


def _graficas_pronostico(asignaturas, resultados, stats_grupo):
    graficas = {}
    if stats_grupo.get('total'):
        graficas['pastel'] = grafica_pastel_aprobacion(
            stats_grupo['aprobados'],
            stats_grupo['reprobados'],
            'Estimación del grupo',
            etiquetas=('Sobre mínimo (estimado)', 'Bajo mínimo (estimado)'),
        )
    riesgo = _conteo_riesgo_asignaturas(asignaturas, resultados)
    if any(cantidad > 0 for cantidad in riesgo.values()):
        graficas['riesgo'] = grafica_riesgo_asignaturas(
            riesgo,
            titulo='Estimaciones bajo mínimo por asignatura',
            ylabel='Estimaciones bajo mínimo',
        )
    return graficas


def _conteo_riesgo_asignaturas(asignaturas, resultados):
    return {
        asignatura.nombre[:16]: sum(
            1 for resultado in resultados
            for item in resultado['asignaturas']
            if item['asignatura'].id == asignatura.id and item['riesgo']
        )
        for asignatura in asignaturas
    }


def solicitar_revision_pronostico(request):
    pronostico_id = (
        request.POST.get('pronostico')
        if request.method == 'POST'
        else request.GET.get('pronostico')
    )
    pronostico_indicado = bool(pronostico_id)
    data = {
        'numero_cuenta': request.POST.get('numero_cuenta', '').strip(),
        'solicitante': request.POST.get('solicitante', '').strip(),
        'parentesco': request.POST.get('parentesco', ''),
        'contacto_email': request.POST.get('contacto_email', '').strip(),
        'motivo': request.POST.get('motivo', '').strip(),
    }
    log = _buscar_log_revision(pronostico_id)
    errores = []
    if request.method == 'GET' and pronostico_indicado and log is None:
        errores.append('No fue posible validar el pronóstico. Vuelve a solicitar el enlace desde coordinación.')
    if request.method == 'POST':
        respuesta, errores = _procesar_post_revision_pronostico(
            request, data, pronostico_id, log,
        )
        if respuesta is not None:
            return respuesta

    return render(request, 'escolar/solicitud_revision_pronostico.html', {
        'errores': errores,
        'form_data': data,
        'pronostico_id': pronostico_id,
        'parentesco_choices': SolicitudRevisionPronostico.PARENTESCO_CHOICES,
    })


def _procesar_post_revision_pronostico(request, data, pronostico_id, log):
    errores = _validar_solicitud_revision(data, request)
    if errores:
        return None, errores
    if not pronostico_id:
        log = _buscar_log_revision_por_cuenta(data['numero_cuenta'])
    if not _log_corresponde_a_cuenta(log, data['numero_cuenta']):
        errores.append('No fue posible validar el pronóstico con esos datos.')
        return None, errores
    solicitud = _crear_solicitud_revision(log, data)
    return render(request, 'escolar/solicitud_revision_pronostico.html', {
        'success_folio': solicitud.pk,
    }), []


def _buscar_log_revision(pronostico_id):
    if not pronostico_id:
        return None
    return PronosticoLog.objects.filter(
        pk=pronostico_id,
        alumno__in=alumnos_con_consentimiento(),
    ).select_related('alumno', 'asignatura').first()


def _validar_solicitud_revision(data, request):
    errores = []
    _validar_datos_identidad_revision(data, errores)
    _validar_datos_contacto_revision(data, errores)
    _validar_motivo_revision(data, errores)
    if request.POST.get('confirmacion') != 'on':
        errores.append('Confirma que la información es correcta.')
    return errores


def _validar_datos_identidad_revision(data, errores):
    if not data['numero_cuenta'] or len(data['numero_cuenta']) > 20:
        errores.append('Indica un número de cuenta válido.')
    if not data['solicitante'] or len(data['solicitante']) > 150:
        errores.append('Indica tu nombre (máximo 150 caracteres).')
    if data['parentesco'] not in dict(SolicitudRevisionPronostico.PARENTESCO_CHOICES):
        errores.append('Selecciona tu relación con el alumno.')


def _validar_datos_contacto_revision(data, errores):
    if not data['contacto_email']:
        errores.append('Indica un correo de contacto.')
    elif not _email_valido(data['contacto_email']):
        errores.append('El correo de contacto no tiene un formato válido.')


def _validar_motivo_revision(data, errores):
    if not data['motivo'] or len(data['motivo']) > 4000:
        errores.append('Describe brevemente la revisión solicitada (máximo 4000 caracteres).')


def _buscar_log_revision_por_cuenta(numero_cuenta):
    alumno = alumnos_con_consentimiento().filter(numero_cuenta=numero_cuenta).first()
    if not alumno:
        return None
    return PronosticoLog.objects.filter(alumno=alumno).select_related(
        'alumno', 'asignatura',
    ).first()


def _log_corresponde_a_cuenta(log, numero_cuenta):
    return log is not None and log.alumno.numero_cuenta == numero_cuenta


def _crear_solicitud_revision(log, data):
    solicitud = SolicitudRevisionPronostico.objects.create(
        alumno=log.alumno,
        pronostico=log,
        solicitante=data['solicitante'],
        parentesco=data['parentesco'],
        contacto_email=data['contacto_email'],
        motivo=data['motivo'],
    )
    SolicitudRevisionPronosticoEvento.objects.create(
        solicitud=solicitud,
        accion='solicitud_recibida',
        detalle='Solicitud de revisión humana recibida.',
    )
    return solicitud


def coordinacion_pronosticos(request):
    if not request.user.is_staff:
        return HttpResponseForbidden('Solo coordinación puede gestionar revisiones de pronóstico.')

    if request.method == 'POST':
        solicitud = get_object_or_404(
            SolicitudRevisionPronostico,
            pk=request.POST.get('solicitud'),
        )
        estado = request.POST.get('estado', '')
        respuesta = request.POST.get('respuesta', '').strip()
        if estado not in dict(SolicitudRevisionPronostico.ESTADO_CHOICES):
            messages.error(request, 'Selecciona un estado válido.')
        elif estado == 'atendida' and not respuesta:
            messages.error(request, 'Registra una respuesta antes de cerrar la revisión.')
        else:
            solicitud.estado = estado
            solicitud.responsable = request.user
            solicitud.respuesta = respuesta
            solicitud.save(update_fields=[
                'estado', 'responsable', 'respuesta', 'fecha_actualizacion',
            ])
            SolicitudRevisionPronosticoEvento.objects.create(
                solicitud=solicitud,
                actor=request.user,
                accion='actualizar_revision',
                detalle=f'Estado actualizado a {solicitud.get_estado_display()}.',
            )
            messages.success(request, 'Seguimiento de revisión actualizado.')
        return redirect(f"{reverse('coordinacion_pronosticos')}?solicitud={solicitud.pk}")

    solicitudes = SolicitudRevisionPronostico.objects.select_related(
        'alumno', 'pronostico__asignatura', 'responsable',
    )
    seleccionada = None
    if request.GET.get('solicitud'):
        seleccionada = get_object_or_404(
            solicitudes,
            pk=request.GET['solicitud'],
        )
    return render(request, 'escolar/coordinacion_pronosticos.html', {
        'solicitudes': solicitudes,
        'seleccionada': seleccionada,
        'estado_choices': SolicitudRevisionPronostico.ESTADO_CHOICES,
    })


# ── Exportar Excel ─────────────────────────────────────────────────────────────

def exportar_excel(request):
    grupo_id = request.GET.get('grupo')
    parcial = request.GET.get('parcial', 'todos')
    if not grupo_id:
        messages.error(request, 'Selecciona un grupo')
        return redirect('lista_alumnos')
    grupo = get_object_or_404(Grupo, id=grupo_id)
    alumnos = list(
        alumnos_con_consentimiento().filter(grupo=grupo).order_by('nombre_completo')
    )
    asignaturas = list(Asignatura.objects.filter(semestre=grupo.semestre))
    parciales_a = [1, 2, 3] if parcial == 'todos' else [int(parcial)]
    calificaciones = {
        (cal.alumno_id, cal.asignatura_id, cal.parcial): cal
        for cal in Calificacion.objects.filter(
            alumno__in=alumnos,
            asignatura__in=asignaturas,
            parcial__in=parciales_a,
        )
    }
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for p in parciales_a:
        _agregar_hoja_trayectoria_excel(
            workbook, grupo, alumnos, asignaturas, calificaciones, p,
        )
    _agregar_hoja_lista_original(workbook, grupo, alumnos)
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="trayectoria_{grupo.semestre.nombre.replace(" ","_")}_G{grupo.nombre}_P{parcial}.xlsx"'
    workbook.save(resp)
    return resp


def _agregar_hoja_trayectoria_excel(workbook, grupo, alumnos, asignaturas, calificaciones, parcial):
    estilo = estilo_excel()
    worksheet = workbook.create_sheet(title=f'Parcial {parcial}')
    worksheet.merge_cells('A1:H1')
    worksheet['A1'] = (
        f'TRAYECTORIA ESCOLAR — {grupo.semestre.nombre} — Grupo {grupo.nombre} '
        f'— Parcial {parcial}  (Mín. aprobatorio: {MINIMO:.0f})'
    )
    worksheet['A1'].font = Font(bold=True, color='FFFFFF', size=12)
    worksheet['A1'].fill = estilo['title_fill']
    worksheet['A1'].alignment = estilo['center']
    worksheet.row_dimensions[1].height = 26
    headers = [
        'N°', 'No. Cuenta', 'Nombre', 'Asignatura', 'Hetero',
        'Coevaluación', 'Autoevaluación', 'Promedio',
    ]
    for column, header in enumerate(headers, 1):
        cell = worksheet.cell(row=2, column=column, value=header)
        cell.font = estilo['header_font']
        cell.fill = estilo['header_fill']
        cell.alignment = estilo['center']
        cell.border = estilo['border']
    for column, width in enumerate([5, 15, 32, 28, 12, 14, 14, 12], 1):
        worksheet.column_dimensions[get_column_letter(column)].width = width
    row_number = 3
    for index, alumno in enumerate(alumnos, 1):
        for asignatura in asignaturas:
            calificacion = calificaciones.get((alumno.id, asignatura.id, parcial))
            _escribir_fila_trayectoria_excel(
                worksheet, row_number, index, alumno, asignatura, calificacion, estilo,
            )
            row_number += 1


def _escribir_fila_trayectoria_excel(
    worksheet, row_number, index, alumno, asignatura, calificacion, estilo,
):
    if calificacion is None:
        fill = estilo['warn_fill']
        values = [
            index, alumno.numero_cuenta, alumno.nombre_completo, asignatura.nombre,
            'N/A', 'N/A', 'N/A', 'N/A',
        ]
    else:
        fill = estilo['aprobado_fill'] if calificacion.aprobado else estilo['reprobado_fill']
        values = [
            index, alumno.numero_cuenta, alumno.nombre_completo, asignatura.nombre,
            float(calificacion.heteroevaluacion), float(calificacion.coevaluacion),
            float(calificacion.autoevaluacion), calificacion.promedio,
        ]
    for column, value in enumerate(values, 1):
        cell = worksheet.cell(row=row_number, column=column, value=value)
        cell.fill = fill
        cell.border = estilo['border']
        cell.alignment = (
            estilo['center'] if column in [1, 2, 5, 6, 7, 8]
            else Alignment(vertical='center')
        )


def _agregar_hoja_lista_original(workbook, grupo, alumnos):
    estilo = estilo_excel()
    worksheet = workbook.create_sheet(title='Lista Original')
    worksheet.merge_cells('A1:D1')
    worksheet['A1'] = f'LISTA DE ALUMNOS — {grupo.semestre.nombre} — Grupo {grupo.nombre}'
    worksheet['A1'].font = Font(bold=True, color='FFFFFF', size=12)
    worksheet['A1'].fill = estilo['title_fill']
    worksheet['A1'].alignment = estilo['center']
    worksheet.row_dimensions[1].height = 24
    for column, header in enumerate(['N°', 'No. Cuenta', 'Nombre Completo', 'Correo'], 1):
        cell = worksheet.cell(row=2, column=column, value=header)
        cell.font = estilo['header_font']
        cell.fill = estilo['header_fill']
        cell.alignment = estilo['center']
        cell.border = estilo['border']
    for column, width in zip(['A', 'B', 'C', 'D'], [5, 15, 40, 35]):
        worksheet.column_dimensions[column].width = width
    alternate_fill = PatternFill(start_color='eef2ff', end_color='eef2ff', fill_type='solid')
    for index, alumno in enumerate(alumnos, 1):
        fill = alternate_fill if index % 2 == 0 else PatternFill(fill_type=None)
        values = [index, alumno.numero_cuenta, alumno.nombre_completo, alumno.email]
        for column, value in enumerate(values, 1):
            cell = worksheet.cell(row=index + 2, column=column, value=value)
            cell.fill = fill
            cell.border = estilo['border']
            cell.alignment = (
                estilo['center'] if column in [1, 2]
                else Alignment(vertical='center')
            )


def exportar_pronostico_excel(request):
    grupo_id = request.GET.get('grupo')
    if not grupo_id:
        return redirect('pronostico')
    grupo = get_object_or_404(Grupo, id=grupo_id)
    alumnos = list(alumnos_con_consentimiento().filter(grupo=grupo).order_by('nombre_completo'))
    asignaturas = list(Asignatura.objects.filter(semestre=grupo.semestre))
    califs_all = list(Calificacion.objects.filter(
        alumno__in=alumnos,
    ).select_related('alumno', 'asignatura'))
    metricas = metricas_grupo_pronostico(califs_all)
    calificaciones_por_alumno = {}
    for calificacion in califs_all:
        calificaciones_por_alumno.setdefault(calificacion.alumno_id, []).append(calificacion)
    estilo = estilo_excel()
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    worksheet = _crear_hoja_pronostico_excel(workbook, grupo, metricas, estilo)
    aprobados, reprobados = _escribir_filas_pronostico_excel(
        worksheet, alumnos, calificaciones_por_alumno, estilo,
    )
    _escribir_resumen_pronostico_excel(worksheet, aprobados, reprobados)

    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="pronostico_IA_{grupo.semestre.nombre.replace(" ","_")}_G{grupo.nombre}.xlsx"'
    workbook.save(resp)
    return resp


def _crear_hoja_pronostico_excel(workbook, grupo, metricas, estilo):
    worksheet = workbook.create_sheet('Pronóstico General')
    worksheet.merge_cells('A1:H1')
    worksheet['A1'] = (
        f'PRONÓSTICO IA — {grupo.semestre.nombre} — Grupo {grupo.nombre}  '
        f'(Mín. aprobatorio: {MINIMO:.0f})'
    )
    worksheet['A1'].font = Font(bold=True, color='FFFFFF', size=12)
    worksheet['A1'].fill = estilo['title_fill']
    worksheet['A1'].alignment = estilo['center']
    worksheet.row_dimensions[1].height = 26
    worksheet.merge_cells('A2:H2')
    worksheet['A2'] = _texto_metricas_pronostico(metricas)
    worksheet['A2'].alignment = Alignment(wrap_text=True)
    headers = [
        'Alumno', 'No. Cuenta', 'P1', 'P2', 'P3 observado',
        'Estimación P3', 'Tendencia', 'Factores (coef. × valor)',
    ]
    for column, header in enumerate(headers, 1):
        cell = worksheet.cell(row=3, column=column, value=header)
        cell.font = estilo['header_font']
        cell.fill = estilo['header_fill']
        cell.alignment = estilo['center']
        cell.border = estilo['border']
    for column, width in enumerate([35, 15, 10, 10, 14, 16, 14, 52], 1):
        worksheet.column_dimensions[get_column_letter(column)].width = width
    return worksheet


def _texto_metricas_pronostico(metricas):
    return (
        'Estimación orientativa; requiere revisión humana. No modifica el estatus académico. '
        f"R²: {metricas['r2'] if metricas['r2'] is not None else 'N/D'} · "
        f"MAE: {metricas['mae'] if metricas['mae'] is not None else 'N/D'} · "
        f"Muestras: {metricas['muestras']} · Modelo: {VERSION_MODELO}"
    )


def _escribir_filas_pronostico_excel(worksheet, alumnos, calificaciones_por_alumno, estilo):
    aprobados = reprobados = 0
    for row_number, alumno in enumerate(alumnos, 4):
        calificaciones = calificaciones_por_alumno.get(alumno.id, [])
        promedios = _promedios_parciales_excel(calificaciones)
        estimacion = explicar_pronostico(promedios)
        pronostico = estimacion['pronostico'] if estimacion else None
        if pronostico is not None and pronostico >= MINIMO:
            aprobados += 1
        elif pronostico is not None:
            reprobados += 1
        _escribir_fila_pronostico_excel(
            worksheet, row_number, alumno, promedios, estimacion, estilo,
        )
    return aprobados, reprobados


def _promedios_parciales_excel(calificaciones):
    por_parcial = {}
    for parcial in (1, 2, 3):
        notas = [
            calificacion.promedio for calificacion in calificaciones
            if calificacion.parcial == parcial
        ]
        if notas:
            por_parcial[parcial] = round(sum(notas) / len(notas), 2)
    return por_parcial


def _escribir_fila_pronostico_excel(
    worksheet, row_number, alumno, promedios, estimacion, estilo,
):
    pronostico = estimacion['pronostico'] if estimacion else None
    tendencia = estimacion['tendencia'] if estimacion else 'Sin datos'
    factores = _texto_factores_pronostico(estimacion)
    fill = (
        estilo['aprobado_fill']
        if pronostico and pronostico >= MINIMO
        else estilo['reprobado_fill']
    )
    values = [
        alumno.nombre_completo,
        alumno.numero_cuenta,
        promedios.get(1, 'N/A'),
        promedios.get(2, 'N/A'),
        promedios.get(3, 'N/A'),
        pronostico if pronostico is not None else '—',
        tendencia,
        factores,
    ]
    for column, value in enumerate(values, 1):
        cell = worksheet.cell(row=row_number, column=column, value=value)
        cell.fill = fill
        cell.border = estilo['border']
        cell.alignment = estilo['center']


def _texto_factores_pronostico(estimacion):
    if not estimacion:
        return 'Sin P1 y P2'
    return '; '.join(
        f"{factor['variable']}: {factor['coeficiente']} × {factor['valor']} = {factor['contribucion']}"
        for factor in estimacion['factores']
    )


def _escribir_resumen_pronostico_excel(worksheet, aprobados, reprobados):
    total = aprobados + reprobados
    porcentaje = round(aprobados / total * 100, 1) if total else 0
    fila = worksheet.max_row + 2
    worksheet.cell(
        row=fila,
        column=1,
        value=(
            f'Estimaciones sobre mínimo: {aprobados}  |  '
            f'Estimaciones bajo mínimo: {reprobados}  |  '
            f'Porcentaje estimado: {porcentaje}%'
        ),
    ).font = Font(bold=True, size=10)


# ── Exportar Resumen por Asignatura (Indicadores) ────────────────────────────

def exportar_resumen_indicadores(request):
    grupo_id = request.GET.get('grupo')
    parcial_filtro = request.GET.get('parcial', '')
    if not grupo_id:
        return redirect('indicadores')
    grupo = get_object_or_404(Grupo, id=grupo_id)
    asignaturas = list(Asignatura.objects.filter(semestre=grupo.semestre))
    califs_all = list(Calificacion.objects.filter(
        alumno__in=alumnos_con_consentimiento().filter(grupo=grupo),
    ).select_related('alumno', 'asignatura'))
    resumen = _resumen_asignaturas_indicadores(
        asignaturas, califs_all, '', parcial_filtro,
    )
    workbook = _crear_libro_resumen_indicadores(grupo, parcial_filtro, resumen)
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="resumen_indicadores_{grupo.semestre.nombre.replace(" ","_")}_G{grupo.nombre}.xlsx"'
    workbook.save(resp)
    return resp


def _crear_libro_resumen_indicadores(grupo, parcial_filtro, resumen):
    estilo = estilo_excel()
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    worksheet = workbook.create_sheet('Resumen Asignaturas')
    titulo = f'RESUMEN POR ASIGNATURA — {grupo.semestre.nombre} — Grupo {grupo.nombre}'
    if parcial_filtro:
        titulo += f' — Parcial {parcial_filtro}'
    worksheet.merge_cells('A1:I1')
    worksheet['A1'] = titulo
    worksheet['A1'].font = Font(bold=True, color='FFFFFF', size=12)
    worksheet['A1'].fill = estilo['title_fill']
    worksheet['A1'].alignment = estilo['center']
    worksheet.row_dimensions[1].height = 26
    headers = [
        'Asignatura', 'Clave', 'Promedio', 'Aprobados', 'Reprobados',
        '% Aprobación', 'P1', 'P2', 'P3',
    ]
    for column, header in enumerate(headers, 1):
        cell = worksheet.cell(row=2, column=column, value=header)
        cell.font = estilo['header_font']
        cell.fill = estilo['header_fill']
        cell.alignment = estilo['center']
        cell.border = estilo['border']
    for column, width in enumerate([35, 12, 12, 12, 12, 16, 10, 10, 10], 1):
        worksheet.column_dimensions[get_column_letter(column)].width = width
    for row_number, item in enumerate(resumen, 3):
        _escribir_fila_resumen_indicadores(worksheet, row_number, item, estilo)
    return workbook


def _escribir_fila_resumen_indicadores(worksheet, row_number, item, estilo):
    fill = estilo['aprobado_fill'] if item['promedio'] >= MINIMO else estilo['reprobado_fill']
    values = [
        item['asignatura'].nombre,
        item['asignatura'].clave,
        item['promedio'],
        item['aprobados'],
        item['reprobados'],
        f"{item['pct_aprobacion']}%",
        item['por_parcial'].get(1, '—'),
        item['por_parcial'].get(2, '—'),
        item['por_parcial'].get(3, '—'),
    ]
    for column, value in enumerate(values, 1):
        cell = worksheet.cell(row=row_number, column=column, value=value)
        cell.fill = fill
        cell.border = estilo['border']
        cell.alignment = estilo['center']


# ── Exportar Índice por Asignatura (Pronóstico) ───────────────────────────────

def exportar_indices_pronostico(request):
    grupo_id = request.GET.get('grupo')
    if not grupo_id:
        return redirect('pronostico')
    grupo = get_object_or_404(Grupo, id=grupo_id)
    alumnos = list(alumnos_con_consentimiento().filter(grupo=grupo).order_by('nombre_completo'))
    asignaturas = list(Asignatura.objects.filter(semestre=grupo.semestre))
    califs_all = list(Calificacion.objects.filter(
        alumno__in=alumnos,
    ).select_related('alumno', 'asignatura'))
    metricas = metricas_grupo_pronostico(califs_all)
    indices = _calcular_indices_pronostico(alumnos, asignaturas, califs_all, metricas)
    workbook = _crear_libro_indices_pronostico(grupo, metricas, indices)
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="indices_pronostico_{grupo.semestre.nombre.replace(" ","_")}_G{grupo.nombre}.xlsx"'
    workbook.save(resp)
    return resp


def _calcular_indices_pronostico(alumnos, asignaturas, calificaciones, metricas):
    por_alumno_asignatura = {}
    for calificacion in calificaciones:
        clave = (calificacion.alumno_id, calificacion.asignatura_id)
        por_alumno_asignatura.setdefault(clave, {})[
            calificacion.parcial
        ] = calificacion.promedio
    indices = []
    for asignatura in asignaturas:
        pronosticos = _pronosticos_asignatura(
            alumnos, asignatura, por_alumno_asignatura, metricas,
        )
        indices.append(_resumen_pronosticos_asignatura(asignatura, pronosticos))
    return indices


def _pronosticos_asignatura(alumnos, asignatura, promedios_por_alumno, metricas):
    pronosticos = []
    for alumno in alumnos:
        parciales = promedios_por_alumno.get((alumno.id, asignatura.id), {})
        estimacion = explicar_pronostico(parciales)
        if estimacion:
            registrar_pronostico(alumno, asignatura, estimacion, metricas)
            pronosticos.append(estimacion['pronostico'])
    return pronosticos


def _resumen_pronosticos_asignatura(asignatura, pronosticos):
    aprobados = sum(1 for promedio in pronosticos if promedio >= MINIMO)
    total = len(pronosticos)
    return {
        'asignatura': asignatura,
        'aprobados': aprobados,
        'reprobados': total - aprobados,
        'total': total,
        'pct_aprobacion': round(aprobados / total * 100, 1) if total else 0,
        'promedio': round(sum(pronosticos) / total, 2) if total else 0,
    }


def _crear_libro_indices_pronostico(grupo, metricas, indices):
    estilo = estilo_excel()
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    worksheet = workbook.create_sheet('Índice Pronóstico')
    worksheet.merge_cells('A1:F1')
    worksheet['A1'] = (
        f'ÍNDICE POR ASIGNATURA (PRONÓSTICO IA) — {grupo.semestre.nombre} '
        f'— Grupo {grupo.nombre}'
    )
    worksheet['A1'].font = Font(bold=True, color='FFFFFF', size=12)
    worksheet['A1'].fill = estilo['title_fill']
    worksheet['A1'].alignment = estilo['center']
    worksheet.row_dimensions[1].height = 26
    worksheet.merge_cells('A2:F2')
    worksheet['A2'] = _texto_metricas_pronostico(metricas)
    worksheet['A2'].alignment = Alignment(wrap_text=True)
    headers = [
        'Asignatura', 'Clave', 'Promedio esperado',
        'Sobre mínimo (estimado)', 'Bajo mínimo (estimado)', '% sobre mínimo',
    ]
    for column, header in enumerate(headers, 1):
        cell = worksheet.cell(row=3, column=column, value=header)
        cell.font = estilo['header_font']
        cell.fill = estilo['header_fill']
        cell.alignment = estilo['center']
        cell.border = estilo['border']
    for column, width in enumerate([35, 12, 18, 12, 12, 16], 1):
        worksheet.column_dimensions[get_column_letter(column)].width = width
    for row_number, item in enumerate(indices, 4):
        _escribir_fila_indice_pronostico(worksheet, row_number, item, estilo)
    return workbook


def _escribir_fila_indice_pronostico(worksheet, row_number, item, estilo):
    fill = estilo['aprobado_fill'] if item['promedio'] >= MINIMO else estilo['reprobado_fill']
    values = [
        item['asignatura'].nombre,
        item['asignatura'].clave,
        item['promedio'],
        item['aprobados'],
        item['reprobados'],
        f"{item['pct_aprobacion']}%",
    ]
    for column, value in enumerate(values, 1):
        cell = worksheet.cell(row=row_number, column=column, value=value)
        cell.fill = fill
        cell.border = estilo['border']
        cell.alignment = estilo['center']


# ── Exportar por asignatura ───────────────────────────────────────────────────

def exportar_asignatura(request):
    asig_id = request.GET.get('asignatura')
    parcial = request.GET.get('parcial', 'todos')
    grupo_id = request.GET.get('grupo')
    if not asig_id:
        return redirect('profesor')
    asig = get_object_or_404(Asignatura, id=asig_id)
    alumnos_qs = alumnos_con_consentimiento().filter(grupo__semestre=asig.semestre)
    if grupo_id:
        alumnos_qs = alumnos_qs.filter(grupo_id=grupo_id)
    alumnos = list(alumnos_qs.order_by('nombre_completo'))
    parciales = [1, 2, 3] if parcial == 'todos' else [int(parcial)]
    calificaciones = {
        (cal.alumno_id, cal.parcial): cal
        for cal in Calificacion.objects.filter(
            alumno__in=alumnos,
            asignatura=asig,
            parcial__in=parciales,
        )
    }
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for parcial_numero in parciales:
        _agregar_hoja_exportacion_asignatura(
            workbook, asig, alumnos, calificaciones, parcial_numero,
        )
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="asignatura_{asig.clave}_P{parcial}.xlsx"'
    workbook.save(resp)
    return resp


def _agregar_hoja_exportacion_asignatura(
    workbook, asignatura, alumnos, calificaciones, parcial,
):
    estilo = estilo_excel()
    worksheet = workbook.create_sheet(title=f'Parcial {parcial}')
    worksheet.merge_cells('A1:H1')
    worksheet['A1'] = (
        f'{asignatura.nombre} ({asignatura.clave}) — Parcial {parcial}  '
        f'(Mín.: {MINIMO:.0f})'
    )
    worksheet['A1'].font = Font(bold=True, color='FFFFFF', size=12)
    worksheet['A1'].fill = estilo['title_fill']
    worksheet['A1'].alignment = estilo['center']
    worksheet.row_dimensions[1].height = 26
    headers = [
        'N°', 'No. Cuenta', 'Nombre', 'Grupo', 'Hetero',
        'Coevaluación', 'Autoevaluación', 'Promedio',
    ]
    for column, header in enumerate(headers, 1):
        cell = worksheet.cell(row=2, column=column, value=header)
        cell.font = estilo['header_font']
        cell.fill = estilo['header_fill']
        cell.alignment = estilo['center']
        cell.border = estilo['border']
    for column, width in enumerate([5, 15, 35, 10, 12, 14, 14, 12], 1):
        worksheet.column_dimensions[get_column_letter(column)].width = width
    for index, alumno in enumerate(alumnos, 1):
        calificacion = calificaciones.get((alumno.id, parcial))
        _escribir_fila_exportacion_asignatura(
            worksheet, index + 2, index, alumno, calificacion, estilo,
        )


def _escribir_fila_exportacion_asignatura(
    worksheet, row_number, index, alumno, calificacion, estilo,
):
    grupo_nombre = alumno.grupo.nombre
    if calificacion is None:
        fill = estilo['warn_fill']
        scores = ['N/A', 'N/A', 'N/A', 'N/A']
    else:
        fill = estilo['aprobado_fill'] if calificacion.aprobado else estilo['reprobado_fill']
        scores = [
            float(calificacion.heteroevaluacion),
            float(calificacion.coevaluacion),
            float(calificacion.autoevaluacion),
            calificacion.promedio,
        ]
    values = [index, alumno.numero_cuenta, alumno.nombre_completo, grupo_nombre, *scores]
    for column, value in enumerate(values, 1):
        cell = worksheet.cell(row=row_number, column=column, value=value)
        cell.fill = fill
        cell.border = estilo['border']
        cell.alignment = estilo['center']


def descargar_plantilla(request):
    e = estilo_excel()
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Calificaciones'
    ws.merge_cells('A1:E1')
    ws['A1'] = f'PLANTILLA DE IMPORTACIÓN — Trayectoria Escolar (Mín. aprobatorio: {MINIMO:.0f})'
    ws['A1'].font = Font(bold=True, color='FFFFFF', size=12)
    ws['A1'].fill = e['title_fill']; ws['A1'].alignment = e['center']
    ws.row_dimensions[1].height = 26
    ws.merge_cells('A2:E2')
    ws['A2'] = 'Instrucciones: Llenar desde la fila 4. Calificaciones en escala 0-10. NO modificar encabezados.'
    ws['A2'].font = Font(italic=True, color='888888', size=10)
    ws['A2'].alignment = Alignment(horizontal='center')
    for col, h in enumerate(['Número de Cuenta', 'Nombre Completo', 'Heteroevaluación', 'Coevaluación', 'Autoevaluación'], 1):
        c = ws.cell(row=3, column=col, value=h)
        c.font = e['header_font']; c.fill = e['header_fill']
        c.alignment = e['center']; c.border = e['border']
    ejemplos = [('2024001','García López María',8.5,7.0,9.0),('2024002','Hernández Juan',6.0,7.5,7.5),('2024003','Martínez Torres Ana',9.0,8.0,8.5)]
    alt = PatternFill(start_color='eef2ff', end_color='eef2ff', fill_type='solid')
    for i, (nc, nm, h, c, a) in enumerate(ejemplos, 4):
        fill_r = alt if i % 2 == 0 else PatternFill(fill_type=None)
        for col, val in enumerate([nc, nm, h, c, a], 1):
            cell = ws.cell(row=i, column=col, value=val)
            cell.fill = fill_r; cell.border = e['border']; cell.alignment = e['center']
    for i, w in enumerate([20, 40, 18, 15, 18], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="plantilla_calificaciones.xlsx"'
    wb.save(resp); return resp


# ── Captura rápida ────────────────────────────────────────────────────────────

def captura_rapida(request):
    grupos = Grupo.objects.select_related('semestre').all()
    grupo_id = request.GET.get('grupo')
    asig_id = request.GET.get('asignatura')
    parcial = request.GET.get('parcial', '1')
    grupo_sel = asig_sel = None
    alumnos = []; asignaturas = []
    alumno_ids_json = '[]'

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))
        if asig_id:
            asig_sel = get_object_or_404(Asignatura, id=asig_id)
            alumnos = list(
                alumnos_con_consentimiento().filter(grupo=grupo_sel).order_by('nombre_completo')
            )
            if alumnos:
                calificaciones = Calificacion.objects.filter(
                    alumno__in=alumnos,
                    asignatura=asig_sel,
                    parcial=int(parcial),
                )
                calificaciones_por_alumno = {
                    calificacion.alumno_id: calificacion
                    for calificacion in calificaciones
                }
                for alumno in alumnos:
                    alumno.cal_existente = calificaciones_por_alumno.get(alumno.id)
            alumno_ids_json = _json.dumps([a.id for a in alumnos])

    return render(request, 'escolar/captura_rapida.html', {
        'grupos': grupos, 'grupo_sel': grupo_sel, 'asig_sel': asig_sel,
        'asignaturas': asignaturas, 'alumnos': alumnos, 'parcial': parcial,
        'alumno_ids_json': alumno_ids_json, 'minimo': MINIMO,
    })


def guardar_calificaciones(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)
    try:
        data = _json.loads(request.body)
        calificaciones, respuesta_error = _parsear_calificaciones_guardadas(
            data.get('rows', []),
        )
        if respuesta_error:
            return respuesta_error
        return _persistir_calificaciones(data, calificaciones)
    except (ValueError, TypeError, KeyError) as ex:
        return JsonResponse({'ok': False, 'error': f'Datos de calificación inválidos: {ex}'}, status=400)
    except Http404:
        return JsonResponse(
            {'ok': False, 'error': 'El alumno no tiene consentimiento vigente.'},
            status=403,
        )
    except Exception as ex:
        return JsonResponse({'ok': False, 'error': str(ex)})


def _parsear_calificaciones_guardadas(filas):
    calificaciones = []
    for index, row in enumerate(filas, 1):
        try:
            calificaciones.append({
                'alumno_id': row['alumno_id'],
                'heteroevaluacion': validar_calificacion(row['hetero']),
                'coevaluacion': validar_calificacion(row['co']),
                'autoevaluacion': validar_calificacion(row['auto']),
            })
        except (KeyError, TypeError, ValueError) as exc:
            return [], JsonResponse({
                'ok': False,
                'error': f'Fila {index}: {exc}',
            }, status=400)
    return calificaciones, None


def _persistir_calificaciones(data, calificaciones):
    asignatura = get_object_or_404(Asignatura, id=data['asignatura_id'])
    parcial = int(data['parcial'])
    alumnos = [
        (
            get_object_or_404(
                alumnos_con_consentimiento(),
                id=fila['alumno_id'],
            ),
            fila,
        )
        for fila in calificaciones
    ]
    creados, actualizados = _actualizar_calificaciones(alumnos, asignatura, parcial)
    return JsonResponse({
        'ok': True,
        'creados': creados,
        'actualizados': actualizados,
    })


def _actualizar_calificaciones(alumnos, asignatura, parcial):
    creados = actualizados = 0
    for alumno, fila in alumnos:
        _, created = Calificacion.objects.update_or_create(
            alumno=alumno,
            asignatura=asignatura,
            parcial=parcial,
            defaults={
                'heteroevaluacion': fila['heteroevaluacion'],
                'coevaluacion': fila['coevaluacion'],
                'autoevaluacion': fila['autoevaluacion'],
            },
        )
        if created:
            creados += 1
        else:
            actualizados += 1
    return creados, actualizados


# ── Seguimiento ───────────────────────────────────────────────────────────────

def seguimiento(request):
    from .models import Nota
    grupos = Grupo.objects.select_related('semestre').all()
    grupo_id = request.GET.get('grupo')
    grupo_sel = None
    alumnos_qs = alumnos_con_consentimiento().select_related('grupo').prefetch_related(
        'notas', 'calificaciones',
    )
    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos_qs = alumnos_qs.filter(grupo=grupo_sel)
    alumnos = list(alumnos_qs)
    for al in alumnos:
        cals = list(al.calificaciones.all())
        al.promedio_general = round(sum(c.promedio for c in cals) / len(cals), 2) if cals else None
    return render(request, 'escolar/seguimiento.html',
                  {'grupos': grupos, 'grupo_sel': grupo_sel, 'alumnos': alumnos, 'minimo': MINIMO})


def seguimiento_notas_api(request):
    from .models import Nota
    alumno = get_object_or_404(alumnos_con_consentimiento(), id=request.GET.get('alumno_id'))
    cals = list(alumno.calificaciones.all())
    prom = round(sum(c.promedio for c in cals) / len(cals), 2) if cals else None
    notas = list(Nota.objects.filter(alumno=alumno).values('tipo', 'texto', 'fecha'))
    for n in notas:
        n['fecha'] = n['fecha'].strftime('%d/%m/%Y %H:%M')
    return JsonResponse({'ok': True, 'notas': notas, 'promedio': prom})


def agregar_nota_api(request):
    from .models import Nota
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    data = _json.loads(request.body)
    alumno = get_object_or_404(alumnos_con_consentimiento(), id=data['alumno_id'])
    Nota.objects.create(alumno=alumno, tipo=data.get('tipo', 'observacion'), texto=data.get('texto', ''))
    return JsonResponse({'ok': True})


# ── Panel Profesor ────────────────────────────────────────────────────────────

def profesor_panel(request):
    pasos_flujo = [
        ('Descarga la plantilla Excel', 'Formato predefinido con las columnas correctas'),
        ('Llena con los datos del grupo', 'Número de cuenta, nombre y las 3 evaluaciones'),
        ('Selecciona grupo, asignatura y parcial', 'El sistema sabe a qué registro corresponde'),
        ('Haz clic en Importar', 'Los alumnos y calificaciones se crean automáticamente'),
    ]
    semestres = Semestre.objects.annotate(
        alumnos_con_consentimiento=Count(
            'grupos__alumnos',
            filter=Q(
                grupos__alumnos__consentimientos__estado=Consentimiento.ESTADO_VIGENTE,
                grupos__alumnos__consentimientos__version_aviso=PRIVACY_NOTICE_VERSION,
            ) if consentimiento_requerido() else Q(),
            distinct=True,
        ),
    )
    return render(request, 'escolar/profesor.html', {
        'semestres': semestres,
        'grupos': grupos_con_conteo_alumnos(),
        'asignaturas': Asignatura.objects.select_related('semestre').all(),
        'pasos_flujo': pasos_flujo,
        'minimo': MINIMO,
    })


def crear_estructura(request):
    if request.method != 'POST':
        return redirect('profesor')
    handlers = {
        'semestre': _crear_semestre,
        'grupo': _crear_grupo,
        'asignatura': _crear_asignatura,
        'alumno': _crear_alumno,
    }
    handler = handlers.get(request.POST.get('accion'))
    if handler:
        handler(request)
    return redirect('profesor')


def _crear_semestre(request):
    nombre = request.POST.get('nombre_semestre', '').strip()
    anio = request.POST.get('anio', '')
    periodo = request.POST.get('periodo', '')
    if not (nombre and anio and periodo):
        messages.error(request, 'Completa todos los campos del semestre.')
        return
    semestre, created = Semestre.objects.get_or_create(
        nombre=nombre, anio=int(anio), periodo=periodo,
    )
    estado = 'creado' if created else 'ya existía'
    messages.success(request, f'Semestre "{semestre.nombre}" {estado}.')


def _crear_grupo(request):
    semestre_id = request.POST.get('semestre_grupo')
    nombre = request.POST.get('nombre_grupo', '').strip()
    if not (semestre_id and nombre):
        messages.error(request, 'Selecciona semestre y nombre de grupo.')
        return
    semestre = get_object_or_404(Semestre, id=semestre_id)
    grupo, created = Grupo.objects.get_or_create(nombre=nombre, semestre=semestre)
    estado = 'creado' if created else 'ya existía'
    messages.success(request, f'Grupo "{grupo.nombre}" {estado}.')


def _crear_asignatura(request):
    semestre_id = request.POST.get('semestre_asig')
    nombre = request.POST.get('nombre_asig', '').strip()
    clave = request.POST.get('clave_asig', '').strip().upper()
    if not (semestre_id and nombre and clave):
        messages.error(request, 'Completa todos los campos de la asignatura.')
        return
    semestre = get_object_or_404(Semestre, id=semestre_id)
    asignatura, created = Asignatura.objects.get_or_create(
        clave=clave,
        defaults={'nombre': nombre, 'semestre': semestre},
    )
    estado = 'creada' if created else 'ya existía'
    messages.success(
        request, f'Asignatura "{asignatura.nombre}" ({asignatura.clave}) {estado}.',
    )


def _crear_alumno(request):
    numero_cuenta = request.POST.get('numero_cuenta', '').strip()
    nombre = request.POST.get('nombre_alumno', '').strip()
    email = request.POST.get('email_alumno', '').strip()
    grupo_id = request.POST.get('grupo_alumno')
    if not (numero_cuenta and nombre and grupo_id):
        messages.error(request, 'Completa número de cuenta, nombre y grupo.')
        return
    grupo = get_object_or_404(Grupo, id=grupo_id)
    alumno, created = Alumno.objects.get_or_create(
        numero_cuenta=numero_cuenta,
        defaults={
            'nombre_completo': nombre,
            'email': email or f'{numero_cuenta}@ithi.edu.mx',
            'grupo': grupo,
        },
    )
    estado = 'agregado' if created else 'ya estaba registrado'
    messages.success(request, f'Alumno "{alumno.nombre_completo}" {estado}.')


def importar_excel(request):
    if request.method != 'POST':
        return redirect('profesor')
    archivo = request.FILES.get('archivo_excel')
    grupo_id = request.POST.get('grupo_importar')
    asig_id = request.POST.get('asignatura_importar')
    parcial = request.POST.get('parcial_importar')
    if not all([archivo, grupo_id, asig_id, parcial]):
        messages.error(request, 'Completa todos los campos antes de importar.')
        return redirect('profesor')
    grupo = get_object_or_404(Grupo, id=grupo_id)
    asig = get_object_or_404(Asignatura, id=asig_id)
    try:
        worksheet = openpyxl.load_workbook(archivo).active
        creados, actualizados, errores = _procesar_filas_importacion(
            worksheet, grupo, asig, parcial,
        )
        _notificar_resultado_importacion(request, creados, actualizados, errores)
    except Exception as ex:
        messages.error(request, f'Error al leer el archivo: {ex}')
    return redirect('profesor')


def _procesar_filas_importacion(worksheet, grupo, asignatura, parcial):
    creados = actualizados = 0
    errores = []
    for row_index, row in enumerate(worksheet.iter_rows(min_row=2, values_only=True), 2):
        if not row or not row[0]:
            continue
        try:
            estado = _importar_fila_calificacion(row, row_index, grupo, asignatura, parcial, errores)
        except Exception as ex:
            errores.append(f'Fila {row_index}: {ex}')
            continue
        if estado == 'creada':
            creados += 1
        elif estado == 'actualizada':
            actualizados += 1
    return creados, actualizados, errores


def _importar_fila_calificacion(row, row_index, grupo, asignatura, parcial, errores):
    numero_cuenta = str(row[0]).strip()
    nombre = str(row[1]).strip() if row[1] else ''
    try:
        notas = [validar_calificacion(row[index]) for index in (2, 3, 4)]
    except ValueError as exc:
        errores.append(f'Fila {row_index}: {exc}')
        return 'error'
    alumno, _ = Alumno.objects.get_or_create(
        numero_cuenta=numero_cuenta,
        defaults={
            'nombre_completo': nombre,
            'email': f'{numero_cuenta}@ithi.edu.mx',
            'grupo': grupo,
        },
    )
    if consentimiento_requerido() and not alumnos_con_consentimiento().filter(
        pk=alumno.pk,
    ).exists():
        errores.append(
            f'Fila {row_index}: se registró el alumno, pero falta consentimiento vigente.',
        )
        return 'error'
    _, created = Calificacion.objects.update_or_create(
        alumno=alumno,
        asignatura=asignatura,
        parcial=int(parcial),
        defaults={
            'heteroevaluacion': notas[0],
            'coevaluacion': notas[1],
            'autoevaluacion': notas[2],
        },
    )
    return 'creada' if created else 'actualizada'


def _notificar_resultado_importacion(request, creados, actualizados, errores):
    mensaje = f'Importación completa: {creados} nuevos, {actualizados} actualizados.'
    if errores:
        messages.warning(request, f'{mensaje} {len(errores)} errores.')
        return
    messages.success(request, mensaje)


# ── Agente IA (Ollama) ────────────────────────────────────────────────────────

def agente_ia(request):
    from django.conf import settings
    return render(request, 'escolar/agente.html', {
        'grupos': Grupo.objects.select_related('semestre').all(),
        'modelo': settings.OLLAMA_MODEL,
    })


def agente_estado_api(request):
    from . import ollama_client
    return JsonResponse(ollama_client.estado())


def agente_alumnos_api(request):
    alumnos = alumnos_con_consentimiento().filter(
        grupo_id=request.GET.get('grupo_id'),
    ).order_by('nombre_completo')
    return JsonResponse({'alumnos': [{'id': a.id, 'nombre': a.nombre_completo} for a in alumnos]})


def agente_chat_api(request):
    from django.http import StreamingHttpResponse
    from . import agente
    from .ollama_client import OllamaError
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)
    try:
        data = _json.loads(request.body)
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)
    historial = data.get('mensajes') or []
    if not historial:
        return JsonResponse({'ok': False, 'error': 'Sin mensajes'}, status=400)
    alumno_id = data.get('alumno_id') or None
    if alumno_id:
        try:
            alumno_id = int(alumno_id)
        except (TypeError, ValueError):
            return JsonResponse({'ok': False, 'error': 'Identificador de alumno inválido'}, status=400)
        if not alumnos_con_consentimiento().filter(pk=alumno_id).exists():
            return JsonResponse(
                {'ok': False, 'error': 'El alumno no tiene consentimiento vigente.'},
                status=403,
            )

    def stream():
        try:
            for trozo in agente.responder_stream(historial, data.get('grupo_id') or None,
                                                 alumno_id):
                yield trozo
        except OllamaError as e:
            yield f"\n\n⚠️ {e}"

    resp = StreamingHttpResponse(stream(), content_type='text/plain; charset=utf-8')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp
