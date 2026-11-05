import base64
import json
import urllib.error
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from . import agente, ollama_client, views
from .middleware import PRIVACY_NOTICE_VERSION
from .privacidad import alumnos_con_consentimiento
from .models import (
    Alumno, Asignatura, AvisoPrivacidadAceptacion, Calificacion, Consentimiento,
    Grupo, IntentoInicioSesion, Nota, Semestre, SolicitudARCO, SolicitudARCOEvento,
    PronosticoLog, SolicitudRevisionPronostico,
)
from .pronosticos import explicar_pronostico, metricas_validacion


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='docente',
            password='clave-segura-prueba',
        )

    def test_application_redirects_anonymous_users_to_login(self):
        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('dashboard')}",
        )

    def test_unknown_login_subpath_redirects_to_login(self):
        response = self.client.get('/login/pronostico/?grupo=2')

        self.assertRedirects(response, reverse('login'))

    def test_internal_urls_redirect_anonymous_users_to_login(self):
        protected_urls = [
            'lista_alumnos',
            'calificaciones',
            'indicadores',
            'metricas_calidad',
            'pronostico',
            'coordinacion_pronosticos',
            'exportar_excel',
            'exportar_pronostico',
            'exportar_resumen_indicadores',
            'exportar_indices_pronostico',
            'profesor',
            'importar_excel',
            'crear_estructura',
            'exportar_asignatura',
            'descargar_plantilla',
            'captura_rapida',
            'guardar_calificaciones',
            'seguimiento',
            'seguimiento_notas_api',
            'agregar_nota_api',
            'gestionar_consentimientos',
            'coordinacion_arco',
            'agente_ia',
            'agente_chat_api',
            'agente_estado_api',
            'agente_alumnos_api',
            'admin:index',
            'privacy_acceptance',
        ]
        protected_urls_with_args = {
            'revocar_consentimiento': [1],
        }

        for url_name in protected_urls:
            with self.subTest(url=url_name):
                url = reverse(url_name)
                response = self.client.get(url)

                self.assertRedirects(
                    response,
                    f"{reverse('login')}?next={url}",
                )

        for url_name, args in protected_urls_with_args.items():
            with self.subTest(url=url_name):
                url = reverse(url_name, args=args)
                response = self.client.get(url)

                self.assertRedirects(
                    response,
                    f"{reverse('login')}?next={url}",
                )

    def test_application_api_requires_login(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('agente_estado_api'))

        self.assertRedirects(
            response,
            f"{reverse('privacy_acceptance')}?next={reverse('agente_estado_api')}",
        )

    def test_login_requires_privacy_acceptance_before_dashboard(self):
        response = self.client.post(reverse('login'), {
            'username': self.user.username,
            'password': 'clave-segura-prueba',
        })

        self.assertRedirects(
            response,
            f"{reverse('privacy_acceptance')}?next={reverse('dashboard')}",
        )
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)
        self.assertEqual(AvisoPrivacidadAceptacion.objects.count(), 0)

    def test_login_blocks_account_after_five_failed_attempts(self):
        for _ in range(4):
            response = self.client.post(reverse('login'), {
                'username': self.user.username,
                'password': 'incorrecta',
            })
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'bloqueado durante 15 minutos')

        response = self.client.post(reverse('login'), {
            'username': self.user.username,
            'password': 'incorrecta',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bloqueado durante 15 minutos')
        intento = IntentoInicioSesion.objects.get(usuario=self.user)
        self.assertEqual(intento.intentos_fallidos, 5)
        self.assertGreater(intento.bloqueado_hasta, timezone.now())

    def test_login_lockout_applies_across_ip_addresses(self):
        for _ in range(5):
            self.client.post(reverse('login'), {
                'username': self.user.username,
                'password': 'incorrecta',
            })

        otro_cliente = Client(REMOTE_ADDR='192.0.2.10')
        response = otro_cliente.post(reverse('login'), {
            'username': self.user.username,
            'password': 'clave-segura-prueba',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bloqueado temporalmente')
        self.assertFalse(otro_cliente.session.get('_auth_user_id'))

    def test_successful_login_clears_failed_attempts(self):
        self.client.post(reverse('login'), {
            'username': self.user.username,
            'password': 'incorrecta',
        })

        response = self.client.post(reverse('login'), {
            'username': self.user.username,
            'password': 'clave-segura-prueba',
        })

        self.assertRedirects(
            response,
            f"{reverse('privacy_acceptance')}?next={reverse('dashboard')}",
        )
        self.assertFalse(IntentoInicioSesion.objects.filter(usuario=self.user).exists())

    def test_login_is_allowed_again_after_lockout_expires(self):
        for _ in range(5):
            self.client.post(reverse('login'), {
                'username': self.user.username,
                'password': 'incorrecta',
            })
        IntentoInicioSesion.objects.filter(usuario=self.user).update(
            bloqueado_hasta=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.post(reverse('login'), {
            'username': self.user.username,
            'password': 'clave-segura-prueba',
        })

        self.assertRedirects(
            response,
            f"{reverse('privacy_acceptance')}?next={reverse('dashboard')}",
        )
        self.assertFalse(IntentoInicioSesion.objects.filter(usuario=self.user).exists())

    def test_accepting_privacy_notice_records_user_and_version(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('privacy_acceptance'), {
            'decision': 'aceptar',
            'aceptacion': 'on',
        })

        self.assertRedirects(response, reverse('dashboard'))
        acceptance = AvisoPrivacidadAceptacion.objects.get()
        self.assertEqual(acceptance.usuario, self.user)
        self.assertEqual(acceptance.version, PRIVACY_NOTICE_VERSION)
        self.assertIsNotNone(acceptance.aceptado_en)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_rejecting_privacy_notice_logs_user_out(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('privacy_acceptance'), {
            'decision': 'rechazar',
        })

        self.assertRedirects(response, reverse('login'))
        self.assertEqual(AvisoPrivacidadAceptacion.objects.count(), 0)
        self.assertRedirects(
            self.client.get(reverse('dashboard')),
            f"{reverse('login')}?next={reverse('dashboard')}",
        )

    def test_acceptance_requires_confirmation_checkbox(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('privacy_acceptance'), {
            'decision': 'aceptar',
        })

        self.assertRedirects(response, reverse('login'))
        self.assertEqual(AvisoPrivacidadAceptacion.objects.count(), 0)

    def test_logout_ends_the_session(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('logout'))

        self.assertRedirects(response, reverse('login'))
        self.assertRedirects(
            self.client.get(reverse('dashboard')),
            f"{reverse('login')}?next={reverse('dashboard')}",
        )

    def test_privacy_notice_is_public(self):
        response = self.client.get(reverse('aviso_privacidad'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aviso de privacidad')
        self.assertContains(response, 'Aviso de privacidad simplificado')
        self.assertContains(response, 'Aviso de privacidad integral')
        self.assertContains(response, PRIVACY_NOTICE_VERSION)
        self.assertContains(response, '26 de octubre de 2026')

    def test_login_footer_links_to_privacy_notice(self):
        response = self.client.get(reverse('login'))

        self.assertContains(response, 'Aviso de privacidad · versión integral y simplificada')


class StudentConsentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='docente-consentimientos',
            password='clave-segura-prueba',
        )
        self.alumno = Alumno.objects.create(
            numero_cuenta='2026001',
            nombre_completo='Alumno Sin Consentimiento',
        )

    def iniciar_sesion_aceptada(self):
        self.client.force_login(self.user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=self.user,
            version=PRIVACY_NOTICE_VERSION,
        )

    def registrar_consentimiento(self):
        return self.client.post(reverse('gestionar_consentimientos'), {
            'alumno': self.alumno.id,
            'tutor_nombre': 'Madre de prueba',
            'parentesco': 'madre',
            'medio': 'presencial',
            'aceptacion': 'on',
        })

    def test_consent_management_requires_login(self):
        response = self.client.get(reverse('gestionar_consentimientos'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_registering_guardian_consent_enables_student_data(self):
        self.iniciar_sesion_aceptada()
        lista_url = reverse('lista_alumnos')
        self.assertContains(
            self.client.get(reverse('gestionar_consentimientos')),
            'Registrar consentimiento',
        )
        self.assertNotContains(self.client.get(lista_url), self.alumno.nombre_completo)

        response = self.registrar_consentimiento()

        self.assertRedirects(response, reverse('gestionar_consentimientos'))
        consentimiento = Consentimiento.objects.get(alumno=self.alumno)
        self.assertEqual(consentimiento.tutor_nombre, 'Madre de prueba')
        self.assertEqual(consentimiento.parentesco, 'madre')
        self.assertEqual(consentimiento.version_aviso, PRIVACY_NOTICE_VERSION)
        self.assertEqual(consentimiento.medio, 'presencial')
        self.assertEqual(consentimiento.estado, Consentimiento.ESTADO_VIGENTE)
        self.assertIsNotNone(consentimiento.fecha_hora)
        self.assertEqual(consentimiento.registrado_por, self.user)
        self.assertContains(self.client.get(lista_url), self.alumno.nombre_completo)

    def test_revoking_consent_blocks_student_data_again(self):
        self.iniciar_sesion_aceptada()
        self.registrar_consentimiento()
        consentimiento = Consentimiento.objects.get(alumno=self.alumno)

        response = self.client.post(
            reverse('revocar_consentimiento', args=[consentimiento.id]),
        )

        self.assertRedirects(response, reverse('gestionar_consentimientos'))
        consentimiento.refresh_from_db()
        self.assertEqual(consentimiento.estado, Consentimiento.ESTADO_REVOCADO)
        self.assertIsNotNone(consentimiento.revocado_en)
        self.assertNotContains(
            self.client.get(reverse('lista_alumnos')),
            self.alumno.nombre_completo,
        )

    def test_direct_student_api_hides_students_without_consent(self):
        self.iniciar_sesion_aceptada()

        response = self.client.get(reverse('seguimiento_notas_api'), {
            'alumno_id': self.alumno.id,
        })

        self.assertEqual(response.status_code, 404)

    def test_ai_api_rejects_unconsented_student_selection(self):
        self.iniciar_sesion_aceptada()

        response = self.client.post(
            reverse('agente_chat_api'),
            data=json.dumps({
                'mensajes': [{'role': 'user', 'content': 'Resume el avance'}],
                'alumno_id': self.alumno.id,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(REQUIRE_STUDENT_CONSENT=False)
    def test_student_data_block_can_be_disabled(self):
        self.iniciar_sesion_aceptada()

        response = self.client.get(reverse('lista_alumnos'))

        self.assertContains(response, self.alumno.nombre_completo)

    def test_admin_hides_unconsented_students_and_shows_privacy_footer(self):
        admin_user = get_user_model().objects.create_superuser(
            username='admin-consentimientos',
            email='admin@example.test',
            password='clave-segura-prueba',
        )
        self.client.force_login(admin_user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=admin_user,
            version=PRIVACY_NOTICE_VERSION,
        )

        response = self.client.get(reverse('admin:escolar_alumno_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.alumno.nombre_completo)
        self.assertContains(response, 'Aviso de privacidad')


class GradeRangeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='docente-calificaciones',
            password='clave-segura-prueba',
        )
        self.semestre = Semestre.objects.create(
            nombre='2026-B',
            anio=2026,
            periodo='AGO-DIC',
        )
        self.grupo = Grupo.objects.create(nombre='B', semestre=self.semestre)
        self.alumno = Alumno.objects.create(
            numero_cuenta='2026003',
            nombre_completo='Alumno Calificaciones',
            grupo=self.grupo,
        )
        self.asignatura = Asignatura.objects.create(
            nombre='Ciencias',
            clave='CIE-01',
            semestre=self.semestre,
        )
        Consentimiento.objects.create(
            alumno=self.alumno,
            tutor_nombre='Tutor de prueba',
            parentesco='tutor',
            version_aviso=PRIVACY_NOTICE_VERSION,
            medio='presencial',
        )
        self.client.force_login(self.user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=self.user,
            version=PRIVACY_NOTICE_VERSION,
        )

    def guardar(self, valores):
        return self.client.post(
            reverse('guardar_calificaciones'),
            data=json.dumps({
                'asignatura_id': self.asignatura.id,
                'parcial': 1,
                'rows': [{
                    'alumno_id': self.alumno.id,
                    'hetero': valores[0],
                    'co': valores[1],
                    'auto': valores[2],
                }],
            }),
            content_type='application/json',
        )

    def test_saves_decimal_scores_within_range(self):
        response = self.guardar(['8.25', '9.5', '0.01'])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        calificacion = Calificacion.objects.get(alumno=self.alumno)
        self.assertEqual(calificacion.heteroevaluacion, Decimal('8.25'))
        self.assertEqual(calificacion.coevaluacion, Decimal('9.5'))
        self.assertEqual(calificacion.autoevaluacion, Decimal('0.01'))

    def test_rejects_out_of_range_scores_without_partial_saves(self):
        for valor in ['-0.01', '10.01']:
            with self.subTest(valor=valor):
                response = self.guardar([valor, '5', '5'])

                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()['ok'])
                self.assertIn('entre 0 y 10', response.json()['error'])
                self.assertFalse(Calificacion.objects.exists())

    def test_rejects_non_finite_or_excess_precision_scores(self):
        for valor in ['NaN', 'Infinity', '8.123']:
            with self.subTest(valor=valor):
                response = self.guardar([valor, '7', '8'])

                self.assertEqual(response.status_code, 400)
                self.assertFalse(Calificacion.objects.exists())

    def test_rejects_non_numeric_score_with_validation_error(self):
        response = self.guardar(['no-es-numero', '7', '8'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('Cada calificación debe ser un número entre 0 y 10.', response.json()['error'])
        self.assertFalse(Calificacion.objects.exists())

    def test_capture_page_displays_range_and_decimal_limits(self):
        response = self.client.get(reverse('captura_rapida'), {
            'grupo': self.grupo.id,
            'asignatura': self.asignatura.id,
        })

        self.assertContains(response, 'Calificaciones permitidas: de 0 a 10.')
        self.assertContains(response, 'Se admiten hasta dos decimales.')
        self.assertContains(response, 'step="0.01"', count=3)


class ResponsibleForecastTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='coordinacion-pronosticos',
            password='clave-segura-prueba',
            is_staff=True,
        )
        self.semestre = Semestre.objects.create(
            nombre='2026-C',
            anio=2026,
            periodo='ENE-JUN',
        )
        self.grupo = Grupo.objects.create(nombre='C', semestre=self.semestre)
        self.alumno = Alumno.objects.create(
            numero_cuenta='2026004',
            nombre_completo='Alumno Pronóstico',
            email='alumno@example.test',
            grupo=self.grupo,
        )
        self.asignatura = Asignatura.objects.create(
            nombre='Historia',
            clave='HIS-01',
            semestre=self.semestre,
        )
        Consentimiento.objects.create(
            alumno=self.alumno,
            tutor_nombre='Tutor de prueba',
            parentesco='tutor',
            version_aviso=PRIVACY_NOTICE_VERSION,
            medio='presencial',
        )
        self.calificaciones = [
            Calificacion.objects.create(
                alumno=self.alumno,
                asignatura=self.asignatura,
                parcial=parcial,
                heteroevaluacion=promedio,
                coevaluacion=promedio,
                autoevaluacion=promedio,
            )
            for parcial, promedio in ((1, 6), (2, 8), (3, 9))
        ]
        self.client.force_login(self.user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=self.user,
            version=PRIVACY_NOTICE_VERSION,
        )

    def generar_pronostico(self):
        return self.client.get(
            reverse('pronostico'),
            {'grupo': self.grupo.id},
        )

    def test_forecast_shows_caveat_explanation_metrics_and_does_not_change_grades(self):
        antes = list(
            Calificacion.objects.filter(alumno=self.alumno)
            .order_by('parcial')
            .values_list('parcial', 'heteroevaluacion', 'coevaluacion', 'autoevaluacion')
        )

        response = self.generar_pronostico()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Estimación orientativa; requiere revisión humana.')
        self.assertContains(response, 'Factores y coeficientes')
        self.assertContains(response, 'Coef. × valor')
        self.assertContains(response, 'R²:')
        self.assertContains(response, 'MAE:')
        self.assertContains(response, f'?pronostico={PronosticoLog.objects.get(alumno=self.alumno).id}')
        log = PronosticoLog.objects.get(alumno=self.alumno, asignatura=self.asignatura)
        self.assertEqual(log.salida['pronostico_p3'], 10)
        self.assertEqual(log.salida['factores'][0]['coeficiente'], -1)
        self.assertEqual(log.salida['factores'][1]['coeficiente'], 2)
        despues = list(
            Calificacion.objects.filter(alumno=self.alumno)
            .order_by('parcial')
            .values_list('parcial', 'heteroevaluacion', 'coevaluacion', 'autoevaluacion')
        )
        self.assertEqual(despues, antes)

    def test_forecast_log_contains_only_permitted_aggregated_features(self):
        self.generar_pronostico()
        log = PronosticoLog.objects.get(alumno=self.alumno)

        self.assertEqual(log.version_modelo, 'tendencia-lineal-v1')
        self.assertEqual(
            set(log.entradas_agregadas),
            {'parciales'},
        )
        self.assertEqual(
            set(log.entradas_agregadas['parciales'][0]),
            {'parcial', 'promedio'},
        )
        for atributo_protegido in (
            'genero', 'edad', 'origen', 'discapacidad', 'nivel_socioeconomico',
        ):
            self.assertNotIn(atributo_protegido, json.dumps(log.entradas_agregadas).lower())

    def test_forecast_excel_exports_use_same_estimate_and_responsible_caveat(self):
        response = self.client.get(
            reverse('exportar_pronostico'),
            {'grupo': self.grupo.id},
        )
        workbook = load_workbook(BytesIO(response.content), data_only=True)
        worksheet = workbook['Pronóstico General']

        self.assertEqual(response.status_code, 200)
        self.assertIn('Estimación orientativa; requiere revisión humana', worksheet['A2'].value)
        self.assertEqual(worksheet['F4'].value, 10)
        self.assertIn('Promedio P1: -1', worksheet['H4'].value)

        index_response = self.client.get(
            reverse('exportar_indices_pronostico'),
            {'grupo': self.grupo.id},
        )
        index_workbook = load_workbook(BytesIO(index_response.content), data_only=True)
        index_worksheet = index_workbook['Índice Pronóstico']
        self.assertIn('Estimación orientativa; requiere revisión humana', index_worksheet['A2'].value)
        self.assertEqual(index_worksheet['D4'].value, 1)
        self.assertEqual(index_worksheet['E4'].value, 0)
        self.assertEqual(PronosticoLog.objects.filter(alumno=self.alumno).count(), 1)

    def test_student_or_guardian_can_submit_human_review_request(self):
        self.generar_pronostico()
        log = PronosticoLog.objects.get(alumno=self.alumno)
        self.client.logout()
        url = reverse('solicitar_revision_pronostico')

        page = self.client.get(url, {'pronostico': log.id})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Solicitar revisión humana')

        response = self.client.post(url, {
            'pronostico': log.id,
            'numero_cuenta': self.alumno.numero_cuenta,
            'solicitante': 'Madre de prueba',
            'parentesco': 'madre',
            'contacto_email': 'madre@example.test',
            'motivo': 'Solicito revisar los factores del cálculo.',
            'confirmacion': 'on',
        })

        solicitud = SolicitudRevisionPronostico.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'REV-{solicitud.id}')
        self.assertEqual(solicitud.alumno, self.alumno)
        self.assertEqual(solicitud.estado, 'recibida')
        self.assertEqual(solicitud.eventos.count(), 1)

    def test_public_review_entry_is_available_from_login_without_forecast_id(self):
        self.generar_pronostico()
        self.client.logout()
        url = reverse('solicitar_revision_pronostico')

        self.assertContains(
            self.client.get(reverse('login')),
            'Solicitar revisión humana de pronóstico',
        )
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            'numero_cuenta': self.alumno.numero_cuenta,
            'solicitante': 'Alumno de prueba',
            'parentesco': 'alumno',
            'contacto_email': 'alumno@example.test',
            'motivo': 'Solicito revisar mi pronóstico.',
            'confirmacion': 'on',
        })

        solicitud = SolicitudRevisionPronostico.objects.get()
        self.assertContains(response, f'REV-{solicitud.id}')
        self.assertEqual(solicitud.pronostico.alumno, self.alumno)

    def test_review_request_requires_valid_student_number(self):
        self.generar_pronostico()
        log = PronosticoLog.objects.get(alumno=self.alumno)
        self.client.logout()

        response = self.client.post(reverse('solicitar_revision_pronostico'), {
            'pronostico': log.id,
            'numero_cuenta': 'incorrecto',
            'solicitante': 'Madre de prueba',
            'parentesco': 'madre',
            'contacto_email': 'madre@example.test',
            'motivo': 'Solicito revisión.',
            'confirmacion': 'on',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No fue posible validar el pronóstico')
        self.assertFalse(SolicitudRevisionPronostico.objects.exists())

    def test_coordination_tracks_review_status_and_response(self):
        self.generar_pronostico()
        log = PronosticoLog.objects.get(alumno=self.alumno)
        solicitud = SolicitudRevisionPronostico.objects.create(
            alumno=self.alumno,
            pronostico=log,
            solicitante='Tutor de prueba',
            parentesco='tutor',
            contacto_email='tutor@example.test',
            motivo='Solicito revisión.',
        )

        response = self.client.post(reverse('coordinacion_pronosticos'), {
            'solicitud': solicitud.id,
            'estado': 'atendida',
            'respuesta': 'Se revisaron los datos y la estimación.',
        })

        self.assertRedirects(
            response,
            f"{reverse('coordinacion_pronosticos')}?solicitud={solicitud.id}",
        )
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'atendida')
        self.assertEqual(solicitud.responsable, self.user)
        self.assertTrue(solicitud.fecha_actualizacion)
        self.assertEqual(solicitud.eventos.count(), 1)

    def test_forecast_review_panel_is_limited_to_staff(self):
        regular_user = get_user_model().objects.create_user(
            username='docente-sin-coordinacion',
            password='clave-segura-prueba',
        )
        self.client.force_login(regular_user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=regular_user,
            version=PRIVACY_NOTICE_VERSION,
        )

        response = self.client.get(reverse('coordinacion_pronosticos'))

        self.assertEqual(response.status_code, 403)

    def test_forecast_helpers_report_metrics_and_require_both_partials(self):
        self.assertIsNone(explicar_pronostico({1: 7}))
        self.assertEqual(metricas_validacion([]), {
            'r2': None,
            'mae': None,
            'muestras': 0,
        })
        self.assertEqual(
            metricas_validacion([(8, 9), (6, 7)]),
            {'r2': 0.0, 'mae': 1.0, 'muestras': 2},
        )


class ArcoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='coordinacion-arco',
            password='clave-segura-prueba',
            is_staff=True,
        )
        self.semestre = Semestre.objects.create(
            nombre='2026-A',
            anio=2026,
            periodo='ENE-JUN',
        )
        self.grupo = Grupo.objects.create(nombre='A', semestre=self.semestre)
        self.alumno = Alumno.objects.create(
            numero_cuenta='2026002',
            nombre_completo='Titular ARCO',
            email='titular@example.test',
            grupo=self.grupo,
        )
        self.asignatura = Asignatura.objects.create(
            nombre='Matemáticas',
            clave='MAT-ARCO',
            semestre=self.semestre,
        )
        self.calificacion = Calificacion.objects.create(
            alumno=self.alumno,
            asignatura=self.asignatura,
            parcial=1,
            heteroevaluacion=8,
            coevaluacion=9,
            autoevaluacion=7,
        )
        self.nota = Nota.objects.create(
            alumno=self.alumno,
            texto='Seguimiento académico privado',
        )
        self.consentimiento = Consentimiento.objects.create(
            alumno=self.alumno,
            tutor_nombre='Tutor del titular',
            parentesco='tutor',
            version_aviso=PRIVACY_NOTICE_VERSION,
            medio='presencial',
        )
        self.datos_solicitud = {
            'tipo': 'acceso',
            'titular_nombre': self.alumno.nombre_completo,
            'numero_cuenta': self.alumno.numero_cuenta,
            'solicitante': 'Solicitante de prueba',
            'parentesco': 'titular',
            'contacto_email': 'solicitante@example.test',
            'descripcion': 'Solicito una copia de mis datos.',
            'confirmacion': 'on',
        }

    def iniciar_sesion_coordinacion(self):
        self.client.force_login(self.user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=self.user,
            version=PRIVACY_NOTICE_VERSION,
        )

    def crear_solicitud(self, tipo='acceso'):
        return SolicitudARCO.objects.create(
            tipo=tipo,
            titular=self.alumno,
            titular_nombre=self.alumno.nombre_completo,
            numero_cuenta=self.alumno.numero_cuenta,
            solicitante='Solicitante de prueba',
            contacto_email='solicitante@example.test',
            descripcion='Solicitud de prueba.',
            estado='identidad_pendiente',
            fecha_limite=timezone.localdate() + timedelta(days=20),
        )

    def verificar_identidad(self, solicitud):
        return self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'verificar_identidad',
            'titular': self.alumno.id,
            'confirmacion_identidad': 'on',
        })

    def test_public_arco_form_creates_request_and_deadline_without_login(self):
        response = self.client.post(reverse('solicitud_arco'), self.datos_solicitud)

        self.assertEqual(response.status_code, 200)
        solicitud = SolicitudARCO.objects.get()
        self.assertContains(response, f'ARCO-{solicitud.id}')
        self.assertEqual(solicitud.tipo, 'acceso')
        self.assertEqual(solicitud.titular, self.alumno)
        self.assertEqual(solicitud.estado, 'identidad_pendiente')
        self.assertEqual(solicitud.fecha_limite, timezone.localdate() + timedelta(days=20))
        self.assertEqual(solicitud.eventos.count(), 1)

    def test_public_arco_form_does_not_reveal_if_titular_exists(self):
        self.client.post(reverse('solicitud_arco'), self.datos_solicitud)
        self.datos_solicitud['numero_cuenta'] = 'no-existe'

        response = self.client.post(reverse('solicitud_arco'), self.datos_solicitud)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SolicitudARCO.objects.count(), 2)
        self.assertEqual(
            SolicitudARCO.objects.order_by('-id').first().titular,
            None,
        )
        self.assertContains(response, 'Recibimos tu solicitud')

    def test_public_arco_form_renders_and_reports_invalid_submission(self):
        page = self.client.get(reverse('solicitud_arco'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Ejercer derechos ARCO')

        invalid_data = {**self.datos_solicitud, 'contacto_email': 'correo-no-valido'}
        invalid_data.pop('confirmacion')
        response = self.client.post(reverse('solicitud_arco'), invalid_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El correo de contacto no tiene un formato válido.')
        self.assertContains(response, 'Confirma que los datos son correctos.')
        self.assertFalse(SolicitudARCO.objects.exists())

    def test_arco_coordination_is_limited_to_staff(self):
        regular_user = get_user_model().objects.create_user(
            username='docente-normal',
            password='clave-segura-prueba',
        )
        self.client.force_login(regular_user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=regular_user,
            version=PRIVACY_NOTICE_VERSION,
        )

        response = self.client.get(reverse('coordinacion_arco'))

        self.assertEqual(response.status_code, 403)

    def test_unverified_identity_cannot_export_student_data(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud()

        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'exportar_json',
        })

        self.assertRedirects(
            response,
            f"{reverse('coordinacion_arco')}?solicitud={solicitud.id}",
        )
        self.assertEqual(solicitud.eventos.count(), 0)

    def test_identity_verification_uses_already_linked_titular(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud()

        page = self.client.get(reverse('coordinacion_arco'), {'solicitud': solicitud.id})
        self.assertContains(page, f'name="titular" value="{self.alumno.id}"')

        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'verificar_identidad',
            'confirmacion_identidad': 'on',
        })

        self.assertRedirects(
            response,
            f"{reverse('coordinacion_arco')}?solicitud={solicitud.id}",
        )
        solicitud.refresh_from_db()
        self.assertTrue(solicitud.identidad_verificada)
        self.assertEqual(solicitud.titular, self.alumno)

    def test_identity_verification_without_a_valid_titular_shows_message(self):
        self.iniciar_sesion_coordinacion()
        solicitud = SolicitudARCO.objects.create(
            tipo='acceso',
            titular_nombre='Titular desconocido',
            numero_cuenta='no-existe',
            solicitante='Solicitante de prueba',
            contacto_email='solicitante@example.test',
            descripcion='Solicitud de prueba.',
            estado='identidad_pendiente',
            fecha_limite=timezone.localdate() + timedelta(days=20),
        )

        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'verificar_identidad',
            'confirmacion_identidad': 'on',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Selecciona un titular válido')
        solicitud.refresh_from_db()
        self.assertFalse(solicitud.identidad_verificada)

    def test_identity_verification_rejects_closed_unconfirmed_and_malformed_titular(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud()
        solicitud.estado = 'atendida'
        solicitud.save(update_fields=['estado'])

        closed_response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'verificar_identidad',
            'confirmacion_identidad': 'on',
        }, follow=True)
        self.assertContains(closed_response, 'Reabre la solicitud antes de continuar el trámite.')

        solicitud.estado = 'identidad_pendiente'
        solicitud.save(update_fields=['estado'])
        unconfirmed_response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'verificar_identidad',
        }, follow=True)
        self.assertContains(unconfirmed_response, 'Confirma que verificaste la identidad')

        invalid_titular_response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'verificar_identidad',
            'titular': 'no-es-un-identificador',
            'confirmacion_identidad': 'on',
        }, follow=True)
        self.assertContains(invalid_titular_response, 'Selecciona un titular válido')
        solicitud.refresh_from_db()
        self.assertFalse(solicitud.identidad_verificada)

    def test_arco_status_updates_validate_and_preserve_verification_rules(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud()

        invalid_status = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'actualizar_solicitud',
            'estado': 'estado-desconocido',
        }, follow=True)
        self.assertContains(invalid_status, 'Selecciona un estado válido.')
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'identidad_pendiente')

        no_response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'actualizar_solicitud',
            'estado': 'atendida',
        }, follow=True)
        self.assertContains(no_response, 'Registra la respuesta antes de cerrar la solicitud.')

        unverified = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'actualizar_solicitud',
            'estado': 'atendida',
            'respuesta': 'Atendida.',
        }, follow=True)
        self.assertContains(unverified, 'Verifica la identidad antes de marcar la solicitud como atendida.')

        solicitud.identidad_verificada = True
        solicitud.fecha_verificacion = timezone.now()
        solicitud.save(update_fields=['identidad_verificada', 'fecha_verificacion'])
        attended = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'actualizar_solicitud',
            'estado': 'atendida',
            'respuesta': 'Solicitud atendida con evidencia.',
        }, follow=True)
        self.assertContains(attended, 'Solicitud actualizada.')
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'atendida')
        self.assertIsNotNone(solicitud.fecha_respuesta)
        self.assertTrue(solicitud.eventos.filter(accion='actualizar_solicitud').exists())

        solicitud.estado = 'en_revision'
        solicitud.save(update_fields=['estado'])
        reopened = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'actualizar_solicitud',
            'estado': 'recibida',
        }, follow=True)
        self.assertContains(reopened, 'Solicitud actualizada.')
        solicitud.refresh_from_db()
        self.assertFalse(solicitud.identidad_verificada)
        self.assertIsNone(solicitud.fecha_verificacion)
        self.assertIsNone(solicitud.fecha_respuesta)

    def test_verified_access_request_exports_json_and_excel_with_audit(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud()
        self.verificar_identidad(solicitud)
        PronosticoLog.objects.create(
            alumno=self.alumno,
            asignatura=self.asignatura,
            version_modelo='tendencia-lineal-v1',
            huella_entradas='e' * 64,
            entradas_agregadas={'parciales': [
                {'parcial': 1, 'promedio': 8},
                {'parcial': 2, 'promedio': 9},
            ]},
            salida={'pronostico_p3': 10},
        )

        response_json = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'exportar_json',
        })
        self.assertEqual(response_json.status_code, 200)
        self.assertEqual(response_json['Content-Type'], 'application/json')
        export_data = json.loads(response_json.content)
        self.assertEqual(export_data['titular']['numero_cuenta'], self.alumno.numero_cuenta)
        self.assertEqual(export_data['calificaciones'][0]['asignatura'], self.asignatura.nombre)
        self.assertEqual(export_data['notas_seguimiento'][0]['texto'], self.nota.texto)
        self.assertEqual(export_data['consentimientos'][0]['tutor'], self.consentimiento.tutor_nombre)
        self.assertEqual(export_data['pronosticos_ia'][0]['salida']['pronostico_p3'], 10)

        response_excel = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'exportar_excel',
        })
        self.assertEqual(response_excel.status_code, 200)
        self.assertIn('spreadsheetml', response_excel['Content-Type'])
        self.assertGreater(len(response_excel.content), 1000)
        workbook = load_workbook(BytesIO(response_excel.content), data_only=True)
        self.assertEqual(workbook['Pronósticos IA']['D2'].value, '{"pronostico_p3": 10}')
        self.assertEqual(
            solicitud.eventos.filter(accion__startswith='exportar_').count(),
            2,
        )

    def test_arco_panel_renders_case_actions_and_export_escapes_excel_formulas(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud()
        self.verificar_identidad(solicitud)

        response = self.client.get(
            reverse('coordinacion_arco'),
            {'solicitud': solicitud.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Descargar JSON')
        self.assertContains(response, 'Bitácora de actuaciones')

        self.alumno.nombre_completo = '=2+2'
        self.alumno.save(update_fields=['nombre_completo'])
        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'exportar_excel',
        })
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        self.assertEqual(workbook['Titular']['B2'].value, "'=2+2")
        self.assertEqual(workbook['Titular']['B2'].data_type, 's')

    def test_rectification_requires_confirmation_and_logs_field_not_old_value(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud('rectificacion')
        self.verificar_identidad(solicitud)

        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'rectificar',
            'campo': 'email',
            'valor': 'corregido@example.test',
        })
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.email, 'titular@example.test')

        self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'rectificar',
            'campo': 'email',
            'valor': 'corregido@example.test',
            'confirmacion': 'on',
        })
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.email, 'corregido@example.test')
        event = solicitud.eventos.get(accion='rectificar')
        self.assertIn('email', event.detalle)
        self.assertNotIn('titular@example.test', event.detalle)

    def test_rectification_rejects_wrong_right_field_and_invalid_email(self):
        self.iniciar_sesion_coordinacion()
        access_request = self.crear_solicitud('acceso')
        self.verificar_identidad(access_request)
        wrong_right = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': access_request.id,
            'accion': 'rectificar',
            'campo': 'email',
            'valor': 'cambio@example.test',
            'confirmacion': 'on',
        }, follow=True)
        self.assertContains(wrong_right, 'Esta solicitud no corresponde al derecho de rectificación.')

        solicitud = self.crear_solicitud('rectificacion')
        self.verificar_identidad(solicitud)
        invalid_field = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'rectificar',
            'campo': 'grupo',
            'valor': 'grupo-ajeno',
            'confirmacion': 'on',
        }, follow=True)
        self.assertContains(invalid_field, 'Selecciona un campo permitido')

        invalid_email = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'rectificar',
            'campo': 'email',
            'valor': 'correo-no-valido',
            'confirmacion': 'on',
        }, follow=True)
        self.assertEqual(invalid_email.status_code, 200)
        self.alumno.refresh_from_db()
        self.assertEqual(self.alumno.email, 'titular@example.test')
        self.assertFalse(solicitud.eventos.filter(accion='rectificar').exists())

    def test_cancelation_block_prevents_student_data_access(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud('cancelacion')
        self.verificar_identidad(solicitud)

        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'bloquear',
            'confirmacion': 'BLOQUEAR',
        })

        self.assertRedirects(
            response,
            f"{reverse('coordinacion_arco')}?solicitud={solicitud.id}",
        )
        self.alumno.refresh_from_db()
        self.assertTrue(self.alumno.bloqueado_arco)
        self.assertFalse(alumnos_con_consentimiento().filter(pk=self.alumno.pk).exists())
        self.assertTrue(solicitud.eventos.filter(accion='bloquear').exists())

    def test_blocking_rejects_incompatible_request_and_missing_confirmation(self):
        self.iniciar_sesion_coordinacion()
        access_request = self.crear_solicitud('acceso')
        self.verificar_identidad(access_request)
        wrong_right = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': access_request.id,
            'accion': 'bloquear',
            'confirmacion': 'BLOQUEAR',
        }, follow=True)
        self.assertContains(wrong_right, 'La acción no corresponde al derecho solicitado.')

        cancel_request = self.crear_solicitud('cancelacion')
        self.verificar_identidad(cancel_request)
        unconfirmed = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': cancel_request.id,
            'accion': 'bloquear',
        }, follow=True)
        self.assertContains(unconfirmed, 'Escribe BLOQUEAR para confirmar la acción.')
        self.alumno.refresh_from_db()
        self.assertFalse(self.alumno.bloqueado_arco)

    def test_unlock_requires_confirmation_and_restores_data_access(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud('cancelacion')
        self.verificar_identidad(solicitud)
        self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'bloquear',
            'confirmacion': 'BLOQUEAR',
        })

        unconfirmed = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'desbloquear',
        }, follow=True)
        self.assertContains(unconfirmed, 'Escribe DESBLOQUEAR para confirmar la acción.')
        self.assertFalse(alumnos_con_consentimiento().filter(pk=self.alumno.pk).exists())

        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'desbloquear',
            'confirmacion': 'DESBLOQUEAR',
        })
        self.assertRedirects(
            response,
            f"{reverse('coordinacion_arco')}?solicitud={solicitud.id}",
        )
        self.alumno.refresh_from_db()
        self.assertFalse(self.alumno.bloqueado_arco)
        self.assertTrue(alumnos_con_consentimiento().filter(pk=self.alumno.pk).exists())
        self.assertTrue(solicitud.eventos.filter(accion='desbloquear').exists())

    def test_suppression_requires_cancelation_right_and_explicit_confirmation(self):
        self.iniciar_sesion_coordinacion()
        access_request = self.crear_solicitud('acceso')
        self.verificar_identidad(access_request)
        wrong_right = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': access_request.id,
            'accion': 'suprimir',
            'confirmacion': 'SUPRIMIR',
        }, follow=True)
        self.assertContains(wrong_right, 'Esta solicitud no corresponde al derecho de cancelación.')

        cancel_request = self.crear_solicitud('cancelacion')
        self.verificar_identidad(cancel_request)
        unconfirmed = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': cancel_request.id,
            'accion': 'suprimir',
        }, follow=True)
        self.assertContains(unconfirmed, 'Escribe SUPRIMIR para confirmar la anonimización.')
        self.alumno.refresh_from_db()
        self.assertFalse(self.alumno.suprimido_arco)

    def test_suppression_anonymizes_holder_deletes_academic_data_and_keeps_audit(self):
        self.iniciar_sesion_coordinacion()
        solicitud = self.crear_solicitud('cancelacion')
        self.verificar_identidad(solicitud)
        pronostico = PronosticoLog.objects.create(
            alumno=self.alumno,
            asignatura=self.asignatura,
            version_modelo='tendencia-lineal-v1',
            huella_entradas='f' * 64,
            entradas_agregadas={'parciales': []},
            salida={'pronostico_p3': 7},
        )
        revision = SolicitudRevisionPronostico.objects.create(
            alumno=self.alumno,
            pronostico=pronostico,
            solicitante='Tutor',
            parentesco='tutor',
            contacto_email='tutor@example.test',
            motivo='Revisar.',
        )

        response = self.client.post(reverse('coordinacion_arco'), {
            'solicitud': solicitud.id,
            'accion': 'suprimir',
            'confirmacion': 'SUPRIMIR',
        })

        self.assertRedirects(
            response,
            f"{reverse('coordinacion_arco')}?solicitud={solicitud.id}",
        )
        self.alumno.refresh_from_db()
        self.assertTrue(self.alumno.suprimido_arco)
        self.assertTrue(self.alumno.bloqueado_arco)
        self.assertEqual(self.alumno.nombre_completo, f'Registro anonimizado {self.alumno.pk}')
        self.assertEqual(self.alumno.email, '')
        self.assertIsNone(self.alumno.grupo)
        self.assertFalse(Calificacion.objects.filter(alumno=self.alumno).exists())
        self.assertFalse(Nota.objects.filter(alumno=self.alumno).exists())
        self.assertFalse(PronosticoLog.objects.filter(alumno=self.alumno).exists())
        revision.refresh_from_db()
        self.assertIsNone(revision.alumno)
        self.assertIsNone(revision.pronostico)
        self.assertEqual(revision.solicitante, '')
        self.assertEqual(revision.contacto_email, '')
        self.assertEqual(revision.motivo, '')
        self.consentimiento.refresh_from_db()
        self.assertIsNone(self.consentimiento.alumno)
        self.assertEqual(self.consentimiento.tutor_nombre, '')
        solicitud.refresh_from_db()
        self.assertIsNone(solicitud.titular)
        self.assertEqual(solicitud.titular_nombre, '')
        self.assertEqual(solicitud.numero_cuenta, '')
        self.assertEqual(solicitud.solicitante, '')
        self.assertEqual(solicitud.contacto_email, '')
        self.assertEqual(solicitud.estado, 'atendida')
        self.assertTrue(solicitud.eventos.filter(accion='suprimir').exists())
        self.assertNotIn(
            self.alumno.numero_cuenta,
            solicitud.eventos.get(accion='suprimir').detalle,
        )


class QualityMetricsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='docente-calidad',
            password='clave-segura-prueba',
        )
        self.client.force_login(self.user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=self.user,
            version=PRIVACY_NOTICE_VERSION,
        )

    def test_quality_dashboard_shows_all_manual_metrics_and_results(self):
        response = self.client.get(reverse('metricas_calidad'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['modo_demostrativo'])
        self.assertEqual(len(response.context['metricas']), 10)
        estados = [metrica['estado'] for metrica in response.context['metricas']]
        self.assertEqual(estados, [
            'CUMPLE',
            'CUMPLE',
            'PENDIENTE',
            'PENDIENTE',
            'PENDIENTE',
            'CUMPLE',
            'PENDIENTE',
            'PENDIENTE',
            'PENDIENTE',
            'PENDIENTE',
        ])
        resultados = [metrica['resultado'] for metrica in response.context['metricas']]
        self.assertEqual(resultados, [
            '94%',
            '9 máxima',
            'PENDIENTE DE MEDICIÓN',
            'PENDIENTE DE MEDICIÓN',
            'PENDIENTE DE MEDICIÓN',
            '0 críticas conocidas; 2 avisos Medium de Bandit',
            'PENDIENTE DE MEDICIÓN',
            'PENDIENTE DE MEDICIÓN',
            'PENDIENTE DE MEDICIÓN',
            'PENDIENTE DE MEDICIÓN',
        ])
        self.assertTrue(all(metrica['fuente'] for metrica in response.context['metricas']))
        self.assertTrue(all(metrica['fecha'] for metrica in response.context['metricas']))
        self.assertTrue(all(metrica['evidencia'] for metrica in response.context['metricas']))
        self.assertTrue(all(
            metrica['explicacion'] for metrica in response.context['metricas']
        ))
        self.assertContains(response, 'Métricas de Calidad')
        self.assertContains(response, 'Cobertura de código')
        self.assertContains(response, '94%')
        self.assertContains(response, 'pytest-cov')
        self.assertContains(response, 'Complejidad ciclomática')
        self.assertContains(response, '9 máxima')
        self.assertContains(response, '0 críticas conocidas; 2 avisos Medium de Bandit')
        self.assertContains(response, '2026-11-05')
        self.assertContains(response, 'Evidencia / comando')
        self.assertContains(response, 'no known vulnerabilities')
        self.assertContains(response, 'Densidad de defectos')
        self.assertContains(response, 'PENDIENTE DE MEDICIÓN')
        self.assertContains(response, 'CUMPLE')
        self.assertContains(response, 'PENDIENTE')

    def test_quality_dashboard_demonstration_mode_is_simulated_and_reversible(self):
        response = self.client.get(reverse('metricas_calidad'), {'modo': 'demostrativo'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['modo_demostrativo'])
        metricas = {metrica['nombre']: metrica for metrica in response.context['metricas']}
        esperadas = {
            'Cobertura de código': ('94%', '≥ 80%', 'CUMPLE'),
            'Complejidad ciclomática': ('9 máxima', '< 10 por método', 'CUMPLE'),
            'Densidad de defectos': ('0.65 defectos/KLOC', '<1.0', 'CUMPLE'),
            'DRE': ('97%', '>95%', 'CUMPLE'),
            'Deuda técnica': ('4.2%', '<5%', 'CUMPLE'),
            'Vulnerabilidades críticas': (
                '0 críticas conocidas; 2 avisos Medium de Bandit',
                '= 0',
                'CUMPLE',
            ),
            'Uptime': ('99.7%', '≥99.5%', 'CUMPLE'),
            'Tiempo de respuesta p95': ('1.4 segundos', '<2 segundos', 'CUMPLE'),
            'ICP': ('100%', '100%', 'CUMPLE'),
            'UAT': ('Aprobado (escenario demostrativo)', 'Aprobado', 'CUMPLE'),
        }
        for nombre, (resultado, meta, estado) in esperadas.items():
            with self.subTest(metrica=nombre):
                self.assertEqual(
                    (metricas[nombre]['resultado'], metricas[nombre]['meta'], metricas[nombre]['estado']),
                    (resultado, meta, estado),
                )

        for nombre in (
            'Cobertura de código',
            'Complejidad ciclomática',
            'Vulnerabilidades críticas',
        ):
            self.assertEqual(metricas[nombre]['fuente'] != 'Escenario demostrativo', True)
        for nombre in (
            'Densidad de defectos',
            'DRE',
            'Deuda técnica',
            'Uptime',
            'Tiempo de respuesta p95',
            'ICP',
            'UAT',
        ):
            self.assertEqual(metricas[nombre]['fuente'], 'Escenario demostrativo')
            self.assertIn('Dato simulado', metricas[nombre]['evidencia'])
            self.assertEqual(metricas[nombre]['fecha'], 'No aplica: valor ilustrativo')

        self.assertContains(
            response,
            'Modo demostrativo: los resultados de las métricas pendientes son valores ilustrativos y no representan mediciones verificadas del sistema.',
        )
        self.assertContains(response, 'Volver a resultados reales')
        self.assertNotContains(response, 'SonarQube')

        real_response = self.client.get(reverse('metricas_calidad'))
        reales = {metrica['nombre']: metrica for metrica in real_response.context['metricas']}
        self.assertFalse(real_response.context['modo_demostrativo'])
        self.assertEqual(reales['Densidad de defectos']['resultado'], 'PENDIENTE DE MEDICIÓN')
        self.assertEqual(reales['Densidad de defectos']['estado'], 'PENDIENTE')
        self.assertContains(real_response, 'Ver modo demostrativo')

    def test_quality_dashboard_lists_standards_with_evidence_based_statuses(self):
        response = self.client.get(reverse('metricas_calidad'))

        self.assertEqual(len(response.context['estandares']), 8)
        self.assertEqual(
            [estandar['nombre'] for estandar in response.context['estandares']],
            [
                'ISO/IEC 25010',
                'ISO/IEC 29119',
                'CMMI para Desarrollo v2.0',
                'IEEE 730',
                'ISO 31000 / PMBOK',
                'OWASP Top 10',
                'LFPDPPP',
                'NOM-151',
            ],
        )
        estados_esperados = [
            'Pendiente de evidencia',
            'Aplicado en el proyecto',
            'Aplicado en el proyecto',
            'Aplicado en el proyecto',
            'Aplicado en el proyecto',
            'Aplicado en el proyecto',
            'Aplicado en el proyecto',
            'Pendiente de evidencia',
        ]
        self.assertEqual(
            [estandar['estado'] for estandar in response.context['estandares']],
            estados_esperados,
        )
        self.assertTrue(all(
            estandar['proposito'] and estandar['aplicacion']
            for estandar in response.context['estandares']
        ))
        self.assertContains(response, 'Estándares y Marcos de Calidad')
        self.assertContains(response, 'Pendiente de evidencia')

    def test_quality_dashboard_is_available_from_the_analysis_menu(self):
        response = self.client.get(reverse('metricas_calidad'))

        self.assertContains(response, reverse('metricas_calidad'))
        self.assertContains(response, 'Métricas de Calidad')


class AcademicAgentTests(TestCase):
    def setUp(self):
        self.semestre = Semestre.objects.create(
            nombre='Agente 2026',
            anio=2026,
            periodo='AGO-DIC',
        )
        self.grupo = Grupo.objects.create(nombre='A', semestre=self.semestre)
        self.asignatura = Asignatura.objects.create(
            nombre='Matemáticas',
            clave='MAT-AG',
            semestre=self.semestre,
        )
        self.alumno = Alumno.objects.create(
            numero_cuenta='AG-001',
            nombre_completo='Alumna Consentida',
            grupo=self.grupo,
        )
        Consentimiento.objects.create(
            alumno=self.alumno,
            tutor_nombre='Tutor de prueba',
            parentesco='tutor',
            version_aviso=PRIVACY_NOTICE_VERSION,
            medio='presencial',
        )
        self.alumno_sin_consentimiento = Alumno.objects.create(
            numero_cuenta='AG-002',
            nombre_completo='Alumno Oculto',
            grupo=self.grupo,
        )
        for parcial, calificacion in ((1, 8), (2, 5), (3, 6)):
            Calificacion.objects.create(
                alumno=self.alumno,
                asignatura=self.asignatura,
                parcial=parcial,
                heteroevaluacion=calificacion,
                coevaluacion=calificacion,
                autoevaluacion=calificacion,
            )
        Nota.objects.create(
            alumno=self.alumno,
            tipo='seguimiento',
            texto='Acordó asesoría semanal.',
        )

    def test_group_context_uses_consenting_students_and_reports_forecast_risk(self):
        contexto = agente.contexto_grupo(self.grupo)

        self.assertIn('GRUPO: Grupo A - Agente 2026 2026 — 1 alumnos.', contexto)
        self.assertIn('- Matemáticas: P1=8.00, P2=5.00, P3=6.00', contexto)
        self.assertIn('Alumna Consentida (AG-001): P1=8.0, P2=5.0, P3=6.0', contexto)
        self.assertIn('RIESGO en: Matemáticas (2.0)', contexto)
        self.assertNotIn(self.alumno_sin_consentimiento.numero_cuenta, contexto)

    def test_student_context_includes_scores_and_recent_notes(self):
        contexto = agente.contexto_alumno(self.alumno)

        self.assertIn('ALUMNO: Alumna Consentida (AG-001)', contexto)
        self.assertIn('Matemáticas P1: hetero=8.00, co=8.00, auto=8.00, promedio=8.0', contexto)
        self.assertIn('Notas del tutor (recientes):', contexto)
        self.assertIn('Acordó asesoría semanal.', contexto)

    def test_messages_filter_history_limit_context_and_missing_ids(self):
        historial = [
            {'role': 'user', 'content': f'mensaje {i}'}
            for i in range(13)
        ] + [
            {'role': 'system', 'content': 'no debe reenviarse'},
            {'role': 'assistant', 'content': '   '},
            {'role': 'assistant', 'content': 'respuesta válida'},
        ]

        mensajes = agente.construir_mensajes(
            historial,
            grupo_id=self.grupo.id,
            alumno_id=self.alumno_sin_consentimiento.id,
        )

        self.assertEqual(mensajes[0]['role'], 'system')
        self.assertIn('Matemáticas', mensajes[0]['content'])
        self.assertNotIn('Alumno Oculto', mensajes[0]['content'])
        self.assertEqual(len(mensajes), 11)
        self.assertEqual(mensajes[1]['content'], 'mensaje 4')
        self.assertEqual(mensajes[-1], {'role': 'assistant', 'content': 'respuesta válida'})
        self.assertNotIn('no debe reenviarse', str(mensajes))

        general = agente.construir_mensajes([{'role': 'user', 'content': 'Hola'}])
        self.assertIn('No se seleccionó grupo ni alumno', general[0]['content'])

    @patch('escolar.agente.chat_stream')
    def test_responder_stream_delegates_built_messages(self, chat_stream):
        historial = [{'role': 'user', 'content': 'Resume el grupo'}]

        resultado = agente.responder_stream(historial, grupo_id=self.grupo.id)

        self.assertIs(resultado, chat_stream.return_value)
        self.assertEqual(chat_stream.call_args.args[0][1], historial[0])


class OllamaClientTests(TestCase):
    @override_settings(OLLAMA_HOST='http://ollama.test/', OLLAMA_MODEL='gemma:4b')
    @patch('escolar.ollama_client.urllib.request.urlopen')
    def test_status_matches_model_tag_variants(self, urlopen):
        urlopen.return_value = BytesIO(json.dumps({
            'models': [{'name': 'gemma:4b'}],
        }).encode())

        status = ollama_client.estado()

        self.assertEqual(status, {
            'servidor': True,
            'modelo': 'gemma:4b',
            'modelo_instalado': True,
            'instalados': ['gemma:4b'],
        })
        self.assertEqual(urlopen.call_args.args[0], 'http://ollama.test/api/tags')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 5)

    @override_settings(OLLAMA_MODEL='gemma:4b')
    @patch('escolar.ollama_client.urllib.request.urlopen', side_effect=OSError('offline'))
    def test_status_reports_unavailable_server(self, urlopen):
        status = ollama_client.estado()

        self.assertFalse(status['servidor'])
        self.assertFalse(status['modelo_instalado'])
        self.assertEqual(status['modelo'], 'gemma:4b')
        self.assertIn('offline', status['error'])

    @override_settings(
        OLLAMA_HOST='http://ollama.test/',
        OLLAMA_MODEL='gemma:4b',
        OLLAMA_TIMEOUT=23,
    )
    @patch('escolar.ollama_client.urllib.request.urlopen')
    def test_chat_stream_skips_empty_lines_and_stops_when_done(self, urlopen):
        urlopen.return_value = BytesIO(
            b'\n'
            b'{"message":{"content":"Hola "}}\n'
            b'{"message":{"content":"mundo"},"done":true}\n'
            b'{"message":{"content":"extra"}}\n'
        )

        respuesta = list(ollama_client.chat_stream([{'role': 'user', 'content': 'Hola'}]))

        self.assertEqual(respuesta, ['Hola ', 'mundo'])
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'http://ollama.test/api/chat')
        self.assertEqual(payload['model'], 'gemma:4b')
        self.assertTrue(payload['stream'])
        self.assertEqual(payload['options']['temperature'], 0.3)
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 23)

    @patch('escolar.ollama_client.urllib.request.urlopen')
    def test_chat_stream_raises_server_reported_error(self, urlopen):
        urlopen.return_value = BytesIO(b'{"error":"modelo no disponible"}\n')

        with self.assertRaisesRegex(ollama_client.OllamaError, 'modelo no disponible'):
            list(ollama_client.chat_stream([]))

    @override_settings(OLLAMA_MODEL='gemma:4b')
    @patch('escolar.ollama_client.urllib.request.urlopen')
    def test_chat_stream_translates_http_and_connection_errors(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            'http://ollama.test/api/chat',
            404,
            'Not Found',
            None,
            BytesIO(b'missing'),
        )
        with self.assertRaisesRegex(ollama_client.OllamaError, 'ollama pull gemma:4b'):
            list(ollama_client.chat_stream([]))

        urlopen.side_effect = urllib.error.HTTPError(
            'http://ollama.test/api/chat',
            503,
            'Unavailable',
            None,
            BytesIO(b'maintenance'),
        )
        with self.assertRaisesRegex(ollama_client.OllamaError, 'Ollama respondió 503: maintenance'):
            list(ollama_client.chat_stream([]))

        urlopen.side_effect = urllib.error.URLError('refused')
        with self.assertRaisesRegex(ollama_client.OllamaError, '¿Está corriendo "ollama serve"'):
            list(ollama_client.chat_stream([]))


class SeedDataCommandTests(TestCase):
    def test_seed_command_creates_expected_records_and_is_idempotent(self):
        output = StringIO()

        call_command('seed_data', stdout=output)
        self.assertEqual(Semestre.objects.count(), 1)
        self.assertEqual(Grupo.objects.count(), 2)
        self.assertEqual(Asignatura.objects.count(), 5)
        self.assertEqual(Alumno.objects.count(), 14)
        self.assertEqual(Calificacion.objects.count(), 14 * 5 * 3)
        self.assertIn('Datos de ejemplo cargados exitosamente', output.getvalue())

        calificaciones_iniciales = list(
            Calificacion.objects.order_by('alumno_id', 'asignatura_id', 'parcial')
            .values_list(
                'heteroevaluacion', 'coevaluacion', 'autoevaluacion',
            )
        )
        call_command('seed_data', stdout=StringIO())

        self.assertEqual(Semestre.objects.count(), 1)
        self.assertEqual(Grupo.objects.count(), 2)
        self.assertEqual(Asignatura.objects.count(), 5)
        self.assertEqual(Alumno.objects.count(), 14)
        self.assertEqual(Calificacion.objects.count(), 14 * 5 * 3)
        self.assertEqual(
            list(
                Calificacion.objects.order_by('alumno_id', 'asignatura_id', 'parcial')
                .values_list(
                    'heteroevaluacion', 'coevaluacion', 'autoevaluacion',
                )
            ),
            calificaciones_iniciales,
        )


class QualityChartTests(SimpleTestCase):
    def assert_png(self, image):
        self.assertIsInstance(image, str)
        self.assertEqual(
            base64.b64decode(image)[:8],
            b'\x89PNG\r\n\x1a\n',
        )

    def test_pie_chart_renders_empty_and_populated_results(self):
        self.assert_png(views.grafica_pastel_aprobacion(0, 0))
        self.assert_png(views.grafica_pastel_aprobacion(
            3,
            2,
            etiquetas=('Cumplen', 'Pendientes'),
        ))

    def test_subject_student_and_risk_charts_render_expected_inputs(self):
        self.assert_png(views.grafica_barras_asignaturas(
            ['Matemáticas', 'Historia'],
            [8.5, 6.0],
        ))
        self.assert_png(views.grafica_alumnos_barras({
            'Estudiante con nombre que se recorta al límite visual': 9.0,
            'Estudiante reprobado': 5.0,
        }))
        self.assert_png(views.grafica_alumnos_barras({}))
        self.assert_png(views.grafica_riesgo_asignaturas({
            'Matemáticas': 2,
            'Historia': 0,
        }))
        self.assert_png(views.grafica_riesgo_asignaturas({}))

    def test_evolution_chart_requires_two_partials_and_renders_full_series(self):
        self.assertIsNone(views.grafica_evolucion_parciales({'P1': 6.0}))
        self.assert_png(views.grafica_evolucion_parciales({
            'P1': 6.0,
            'P2': 7.5,
            'P3': 8.0,
        }))

    def test_heatmap_handles_missing_values_and_no_subjects(self):
        self.assertIsNone(views.grafica_calor_asignaturas_parciales([]))
        self.assert_png(views.grafica_calor_asignaturas_parciales([{
            'asignatura': type('AsignaturaStub', (), {'nombre': 'Matemáticas'})(),
            'por_parcial': {1: 3.5, 2: 7.0},
        }]))


class CoreAcademicViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='docente-vistas-core',
            password='clave-segura-prueba',
        )
        self.semestre = Semestre.objects.create(
            nombre='Vistas 2026',
            anio=2026,
            periodo='AGO-DIC',
        )
        self.grupo = Grupo.objects.create(nombre='V', semestre=self.semestre)
        self.matematicas = Asignatura.objects.create(
            nombre='Matemáticas',
            clave='MAT-V',
            semestre=self.semestre,
        )
        self.historia = Asignatura.objects.create(
            nombre='Historia',
            clave='HIS-V',
            semestre=self.semestre,
        )
        self.aprobada = self.crear_alumno('V-001', 'Ana Aprobada')
        self.reprobado = self.crear_alumno('V-002', 'Beto Reprobado')
        self.sin_consentimiento = Alumno.objects.create(
            numero_cuenta='V-003',
            nombre_completo='Cecilia Sin Consentimiento',
            grupo=self.grupo,
        )
        for parcial, valor in ((1, 8), (2, 9), (3, 10)):
            self.crear_calificacion(self.aprobada, self.matematicas, parcial, valor)
        self.crear_calificacion(self.aprobada, self.historia, 1, 7)
        for parcial, valor in ((1, 4), (2, 5), (3, 6)):
            self.crear_calificacion(self.reprobado, self.matematicas, parcial, valor)

        self.client.force_login(self.user)
        AvisoPrivacidadAceptacion.objects.create(
            usuario=self.user,
            version=PRIVACY_NOTICE_VERSION,
        )

    def crear_alumno(self, numero_cuenta, nombre):
        alumno = Alumno.objects.create(
            numero_cuenta=numero_cuenta,
            nombre_completo=nombre,
            grupo=self.grupo,
        )
        Consentimiento.objects.create(
            alumno=alumno,
            tutor_nombre='Tutor de prueba',
            parentesco='tutor',
            version_aviso=PRIVACY_NOTICE_VERSION,
            medio='presencial',
        )
        return alumno

    @staticmethod
    def crear_calificacion(alumno, asignatura, parcial, valor):
        return Calificacion.objects.create(
            alumno=alumno,
            asignatura=asignatura,
            parcial=parcial,
            heteroevaluacion=valor,
            coevaluacion=valor,
            autoevaluacion=valor,
        )

    def test_dashboard_aggregates_real_student_and_grade_counts(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_alumnos'], 2)
        self.assertEqual(response.context['total_calificaciones'], 7)
        self.assertEqual(response.context['aprobados'], 4)
        self.assertEqual(response.context['reprobados'], 3)
        self.assertEqual(response.context['pct_aprobacion'], 57.1)
        self.assertEqual(response.context['pct_reprobacion'], 42.9)
        self.assertEqual(response.context['grupos'][0].alumnos_con_consentimiento, 2)

    def test_student_list_filters_group_and_obeys_current_consent(self):
        response = self.client.get(reverse('lista_alumnos'), {'grupo': self.grupo.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {alumno.pk for alumno in response.context['alumnos']},
            {self.aprobada.pk, self.reprobado.pk},
        )
        self.assertNotContains(response, self.sin_consentimiento.nombre_completo)
        self.assertEqual(response.context['grupo_sel'], self.grupo)

    def test_grade_table_returns_partial_results_and_subject_averages(self):
        response = self.client.get(reverse('calificaciones'), {
            'grupo': self.grupo.id,
            'parcial': '1',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['datos']), 2)
        self.assertEqual(response.context['promedios_col'], {
            self.matematicas.clave: 6.0,
            self.historia.clave: 7.0,
        })
        self.assertIn('asignaturas', response.context['graficas'])
        self.assertIsNone(response.context['datos'][1]['calificaciones'][self.historia.clave])

    def test_grade_table_query_count_does_not_scale_with_students_or_subjects(self):
        url = reverse('calificaciones')
        params = {'grupo': self.grupo.id, 'parcial': '1'}
        with CaptureQueriesContext(connection) as initial_queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        capture_url = reverse('captura_rapida')
        capture_params = {
            'grupo': self.grupo.id,
            'asignatura': self.matematicas.id,
            'parcial': '1',
        }
        with CaptureQueriesContext(connection) as initial_capture_queries:
            response = self.client.get(capture_url, capture_params)
        self.assertEqual(response.status_code, 200)

        nuevas_asignaturas = [
            Asignatura.objects.create(
                nombre=f'Asignatura adicional {index}',
                clave=f'AD-{index}',
                semestre=self.semestre,
            )
            for index in range(3)
        ]
        for index in range(5):
            alumno = self.crear_alumno(f'V-10{index}', f'Alumno adicional {index}')
            for asignatura in nuevas_asignaturas:
                self.crear_calificacion(alumno, asignatura, 1, 8)

        alumnos_con_acceso = list(
            alumnos_con_consentimiento().filter(grupo=self.grupo),
        )
        asignaturas = list(Asignatura.objects.filter(semestre=self.semestre))
        with CaptureQueriesContext(connection) as consultas_implementacion_anterior:
            for alumno in alumnos_con_acceso:
                for asignatura in asignaturas:
                    Calificacion.objects.filter(
                        alumno=alumno,
                        asignatura=asignatura,
                        parcial=1,
                    ).first()

        with CaptureQueriesContext(connection) as expanded_queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(expanded_queries), len(initial_queries))
        self.assertEqual(
            len(consultas_implementacion_anterior),
            len(alumnos_con_acceso) * len(asignaturas),
        )
        self.assertLess(len(expanded_queries), len(consultas_implementacion_anterior))

        with CaptureQueriesContext(connection) as expanded_capture_queries:
            response = self.client.get(capture_url, capture_params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(expanded_capture_queries), len(initial_capture_queries))

    def test_indicators_calculate_group_and_subject_metrics_and_charts(self):
        response = self.client.get(reverse('indicadores'), {'grupo': self.grupo.id})

        stats = response.context['stats']
        self.assertEqual(stats['total'], 2)
        self.assertEqual(stats['aprobados'], 1)
        self.assertEqual(stats['reprobados'], 1)
        self.assertEqual(stats['pct_aprobacion'], 50.0)
        self.assertEqual(stats['promedio_grupo'], 6.75)
        self.assertEqual(
            {item['asignatura'].pk for item in response.context['asignaturas_resumen']},
            {self.matematicas.pk, self.historia.pk},
        )
        matematicas = next(
            item for item in response.context['asignaturas_resumen']
            if item['asignatura'] == self.matematicas
        )
        self.assertEqual(matematicas['por_parcial'], {1: 6.0, 2: 7.0, 3: 8.0})
        self.assertTrue({'pastel', 'evolucion', 'alumnos', 'calor'}.issubset(
            response.context['graficas'],
        ))

    def test_indicators_apply_subject_and_partial_filters(self):
        response = self.client.get(reverse('indicadores'), {
            'grupo': self.grupo.id,
            'asignatura': self.historia.id,
            'parcial': '1',
        })

        self.assertEqual(response.context['asig_filtro'], str(self.historia.id))
        self.assertEqual(response.context['parcial_filtro'], '1')
        self.assertEqual(response.context['stats']['promedio_grupo'], 7.0)
        self.assertEqual(len(response.context['asignaturas_resumen']), 1)
        self.assertEqual(
            response.context['asignaturas_resumen'][0]['asignatura'],
            self.historia,
        )
        self.assertEqual(response.context['graficas'].keys(), {'pastel', 'alumnos'})

    def test_capture_page_exposes_existing_grade_and_empty_partial(self):
        response = self.client.get(reverse('captura_rapida'), {
            'grupo': self.grupo.id,
            'asignatura': self.matematicas.id,
            'parcial': '2',
        })

        self.assertEqual(response.status_code, 200)
        alumnos = response.context['alumnos']
        self.assertEqual(len(alumnos), 2)
        self.assertEqual(
            {alumno.cal_existente.promedio for alumno in alumnos},
            {9.0, 5.0},
        )
        self.assertEqual(response.context['alumno_ids_json'], f'[{self.aprobada.id}, {self.reprobado.id}]')

        no_registrada = self.client.get(reverse('captura_rapida'), {
            'grupo': self.grupo.id,
            'asignatura': self.historia.id,
            'parcial': '3',
        })
        self.assertIsNone(no_registrada.context['alumnos'][0].cal_existente)

    def test_follow_up_calculates_average_and_notes_api_returns_persisted_values(self):
        response = self.client.get(reverse('seguimiento'), {'grupo': self.grupo.id})

        self.assertEqual(len(response.context['alumnos']), 2)
        promedio_ana = next(
            alumno.promedio_general for alumno in response.context['alumnos']
            if alumno.pk == self.aprobada.pk
        )
        self.assertEqual(promedio_ana, 8.5)

        nota_response = self.client.post(
            reverse('agregar_nota_api'),
            data=json.dumps({
                'alumno_id': self.aprobada.id,
                'tipo': 'logro',
                'texto': 'Terminó su proyecto.',
            }),
            content_type='application/json',
        )
        self.assertEqual(nota_response.json(), {'ok': True})
        notas_response = self.client.get(reverse('seguimiento_notas_api'), {
            'alumno_id': self.aprobada.id,
        })
        payload = notas_response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['promedio'], 8.5)
        self.assertEqual(payload['notas'][0]['tipo'], 'logro')
        self.assertEqual(payload['notas'][0]['texto'], 'Terminó su proyecto.')

        self.assertEqual(
            self.client.get(reverse('agregar_nota_api')).json(),
            {'ok': False},
        )

    def test_grade_and_subject_exports_preserve_calculated_values_and_missing_rows(self):
        response = self.client.get(reverse('exportar_excel'), {
            'grupo': self.grupo.id,
            'parcial': '1',
        })

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content), data_only=True)
        parcial = workbook['Parcial 1']
        self.assertEqual(parcial['D3'].value, 'Matemáticas')
        self.assertEqual(parcial['H3'].value, 8)
        self.assertEqual(parcial['D4'].value, 'Historia')
        self.assertEqual(parcial['H4'].value, 7)
        self.assertEqual(parcial['H6'].value, 'N/A')
        lista = workbook['Lista Original']
        self.assertEqual(lista.max_row, 4)
        self.assertEqual(lista['C3'].value, 'Ana Aprobada')
        self.assertEqual(lista['C4'].value, 'Beto Reprobado')

        subject_response = self.client.get(reverse('exportar_asignatura'), {
            'asignatura': self.matematicas.id,
            'grupo': self.grupo.id,
            'parcial': '1',
        })
        subject_workbook = load_workbook(BytesIO(subject_response.content), data_only=True)
        self.assertEqual(subject_workbook.sheetnames, ['Parcial 1'])
        self.assertEqual(subject_workbook['Parcial 1']['H3'].value, 8)
        self.assertEqual(subject_workbook['Parcial 1']['H4'].value, 4)

    def test_summary_export_reports_subject_approval_counts_and_partial_averages(self):
        response = self.client.get(reverse('exportar_resumen_indicadores'), {
            'grupo': self.grupo.id,
        })
        workbook = load_workbook(BytesIO(response.content), data_only=True)
        worksheet = workbook['Resumen Asignaturas']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(worksheet['A1'].value, 'RESUMEN POR ASIGNATURA — Vistas 2026 — Grupo V')
        math_row = next(
            row for row in range(3, worksheet.max_row + 1)
            if worksheet.cell(row=row, column=2).value == self.matematicas.clave
        )
        self.assertEqual(worksheet.cell(math_row, 3).value, 7)
        self.assertEqual(worksheet.cell(math_row, 4).value, 3)
        self.assertEqual(worksheet.cell(math_row, 5).value, 3)
        self.assertEqual(worksheet.cell(math_row, 7).value, 6)
        self.assertEqual(worksheet.cell(math_row, 8).value, 7)
        self.assertEqual(worksheet.cell(math_row, 9).value, 8)

        parcial_response = self.client.get(reverse('exportar_resumen_indicadores'), {
            'grupo': self.grupo.id,
            'parcial': '1',
        })
        parcial_workbook = load_workbook(BytesIO(parcial_response.content), data_only=True)
        self.assertIn('Parcial 1', parcial_workbook['Resumen Asignaturas']['A1'].value)
        self.assertEqual(parcial_workbook['Resumen Asignaturas'].max_row, 4)

    def test_import_uses_consent_and_grade_validation_and_reports_row_errors(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append([
            'Número de Cuenta', 'Nombre', 'Heteroevaluación',
            'Coevaluación', 'Autoevaluación',
        ])
        worksheet.append([self.aprobada.numero_cuenta, 'No reemplazar', 6, 7, 8])
        worksheet.append(['V-004', 'Nuevo sin consentimiento', 8, 8, 8])
        worksheet.append(['V-005', 'Calificación inválida', 11, 8, 8])
        worksheet.append([None, None, None, None, None])
        archivo = BytesIO()
        workbook.save(archivo)
        upload = SimpleUploadedFile(
            'calificaciones.xlsx',
            archivo.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        response = self.client.post(reverse('importar_excel'), {
            'archivo_excel': upload,
            'grupo_importar': self.grupo.id,
            'asignatura_importar': self.matematicas.id,
            'parcial_importar': 1,
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        grade = Calificacion.objects.get(
            alumno=self.aprobada,
            asignatura=self.matematicas,
            parcial=1,
        )
        self.assertEqual(
            (grade.heteroevaluacion, grade.coevaluacion, grade.autoevaluacion),
            (Decimal('6'), Decimal('7'), Decimal('8')),
        )
        alumno_nuevo = Alumno.objects.get(numero_cuenta='V-004')
        self.assertEqual(alumno_nuevo.nombre_completo, 'Nuevo sin consentimiento')
        self.assertFalse(Calificacion.objects.filter(alumno=alumno_nuevo).exists())
        self.assertFalse(Alumno.objects.filter(numero_cuenta='V-005').exists())
        self.assertContains(response, 'Importación completa: 0 nuevos, 1 actualizados. 2 errores.')

    def test_import_reports_missing_fields_and_unreadable_workbook(self):
        missing_fields = self.client.post(reverse('importar_excel'), {}, follow=True)
        self.assertContains(missing_fields, 'Completa todos los campos antes de importar.')

        unreadable = self.client.post(reverse('importar_excel'), {
            'archivo_excel': SimpleUploadedFile('no.xlsx', b'not an excel workbook'),
            'grupo_importar': self.grupo.id,
            'asignatura_importar': self.matematicas.id,
            'parcial_importar': 1,
        }, follow=True)
        self.assertContains(unreadable, 'Error al leer el archivo:')
        self.assertFalse(Calificacion.objects.filter(alumno=self.sin_consentimiento).exists())

    def test_teacher_panel_and_structure_creation_validate_and_preserve_unique_records(self):
        panel = self.client.get(reverse('profesor'))
        self.assertEqual(panel.status_code, 200)
        self.assertEqual(len(panel.context['pasos_flujo']), 4)
        self.assertEqual(panel.context['semestres'][0].alumnos_con_consentimiento, 2)

        create_semester = self.client.post(reverse('crear_estructura'), {
            'accion': 'semestre',
            'nombre_semestre': 'Nuevo semestre',
            'anio': 2027,
            'periodo': 'ENE-JUN',
        })
        self.assertRedirects(create_semester, reverse('profesor'))
        new_semester = Semestre.objects.get(nombre='Nuevo semestre')
        self.client.post(reverse('crear_estructura'), {
            'accion': 'grupo',
            'semestre_grupo': new_semester.id,
            'nombre_grupo': 'G',
        })
        new_group = Grupo.objects.get(nombre='G', semestre=new_semester)
        self.client.post(reverse('crear_estructura'), {
            'accion': 'asignatura',
            'semestre_asig': new_semester.id,
            'nombre_asig': 'Química',
            'clave_asig': 'qui-01',
        })
        new_subject = Asignatura.objects.get(clave='QUI-01')
        self.client.post(reverse('crear_estructura'), {
            'accion': 'alumno',
            'numero_cuenta': 'NEW-10',
            'nombre_alumno': 'Nuevo alumno',
            'grupo_alumno': new_group.id,
        })
        new_student = Alumno.objects.get(numero_cuenta='NEW-10')
        self.assertEqual(new_student.email, 'NEW-10@ithi.edu.mx')
        self.assertEqual(new_student.grupo, new_group)
        self.assertEqual(new_subject.semestre, new_semester)

        self.client.post(reverse('crear_estructura'), {
            'accion': 'alumno',
            'numero_cuenta': 'NEW-10',
            'nombre_alumno': 'Nombre ignorado por unicidad',
            'email_alumno': 'nuevo@example.test',
            'grupo_alumno': new_group.id,
        })
        self.assertEqual(Alumno.objects.filter(numero_cuenta='NEW-10').count(), 1)
        self.assertEqual(Alumno.objects.get(numero_cuenta='NEW-10').nombre_completo, 'Nuevo alumno')

        invalid = self.client.post(reverse('crear_estructura'), {
            'accion': 'semestre',
            'nombre_semestre': '',
            'anio': '',
            'periodo': '',
        }, follow=True)
        self.assertContains(invalid, 'Completa todos los campos del semestre.')

    def test_download_template_contains_documented_columns_and_examples(self):
        response = self.client.get(reverse('descargar_plantilla'))
        workbook = load_workbook(BytesIO(response.content), data_only=True)
        worksheet = workbook['Calificaciones']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(worksheet['A3'].value, 'Número de Cuenta')
        self.assertEqual(worksheet['C3'].value, 'Heteroevaluación')
        self.assertEqual(worksheet['A4'].value, '2024001')
        self.assertEqual(worksheet['C4'].value, 8.5)
