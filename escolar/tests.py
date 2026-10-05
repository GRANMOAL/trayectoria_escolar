import json

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from .models import Alumno, Asignatura, Grupo, Semestre, Calificacion
from .urls import urlpatterns


class AutenticacionTests(TestCase):
    def test_paginas_protegidas_redirigen_a_inicio_sesion(self):
        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('dashboard')}",
            fetch_redirect_response=False,
        )

    def test_panel_admin_tambien_requiere_autenticacion(self):
        response = self.client.get('/admin/')

        self.assertRedirects(
            response,
            f"{reverse('login')}?next=/admin/",
            fetch_redirect_response=False,
        )

    def test_todas_las_rutas_operativas_requieren_autenticacion(self):
        rutas_publicas = {'login', 'registro'}
        rutas_protegidas = [
            route.name for route in urlpatterns if route.name not in rutas_publicas
        ]

        for nombre_ruta in rutas_protegidas:
            with self.subTest(ruta=nombre_ruta):
                response = self.client.get(reverse(nombre_ruta))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url.split('?')[0], reverse('login'))

    def test_registro_guarda_usuario_con_contrasena_sha256_y_sal(self):
        response = self.client.post(reverse('registro'), {
            'username': 'docente.ithi',
            'password1': 'ClaveSegura-2026!',
            'password2': 'ClaveSegura-2026!',
        })

        usuario = get_user_model().objects.get(username='docente.ithi')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(usuario.check_password('ClaveSegura-2026!'))
        self.assertTrue(usuario.password.startswith('pbkdf2_sha256$'))
        self.assertNotIn('ClaveSegura-2026!', usuario.password)
        self.assertTrue(usuario.password.split('$')[2])
        self.assertTrue('_auth_user_id' in self.client.session)

    def test_inicio_sesion_y_cierre_de_sesion(self):
        usuario = get_user_model().objects.create_user(
            username='docente.ithi',
            password='ClaveSegura-2026!',
        )

        response = self.client.post(reverse('login'), {
            'username': usuario.username,
            'password': 'ClaveSegura-2026!',
        })

        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertRedirects(
            self.client.get(reverse('dashboard')),
            f"{reverse('login')}?next={reverse('dashboard')}",
            fetch_redirect_response=False,
        )

    def test_next_externo_no_redirige_fuera_del_sitio(self):
        usuario = get_user_model().objects.create_user(
            username='docente.ithi',
            password='ClaveSegura-2026!',
        )

        response = self.client.post(
            f"{reverse('login')}?next=https://ejemplo.invalid/",
            {
                'username': usuario.username,
                'password': 'ClaveSegura-2026!',
                'next': 'https://ejemplo.invalid/',
            },
        )

        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)


class CapturaRapidaCalificacionesTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_user(
            username='docente.ithi',
            password='ClaveSegura-2026!',
        )
        self.client.force_login(usuario)
        semestre = Semestre.objects.create(
            nombre='Semestre de prueba',
            anio=2026,
            periodo='AGO-DIC',
        )
        grupo = Grupo.objects.create(nombre='A', semestre=semestre)
        self.alumno = Alumno.objects.create(
            numero_cuenta='TEST001',
            nombre_completo='Alumno de prueba',
            grupo=grupo,
        )
        self.asignatura = Asignatura.objects.create(
            nombre='Materia de prueba',
            clave='TEST101',
            semestre=semestre,
        )
        self.grupo = grupo
        self.url = reverse('guardar_calificaciones')
        self.payload = {
            'asignatura_id': self.asignatura.id,
            'parcial': 1,
            'rows': [{
                'alumno_id': self.alumno.id,
                'hetero': 8,
                'co': 8,
                'auto': 8,
            }],
        }

    def test_rechaza_calificacion_negativa_y_mayor_a_diez(self):
        for valor in (-0.1, 10.1):
            with self.subTest(valor=valor):
                self.payload['rows'][0]['hetero'] = valor

                response = self.client.post(
                    self.url,
                    data=json.dumps(self.payload),
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['ok'], False)
                self.assertIn('negativos o superiores a 10', response.json()['error'])
                self.assertFalse(Calificacion.objects.exists())

    def test_plantilla_de_captura_muestra_alerta_de_rango(self):
        response = self.client.get(reverse('captura_rapida'), {
            'grupo': self.grupo.id,
            'asignatura': self.asignatura.id,
        })

        self.assertContains(
            response,
            'No puedes agregar valores negativos o superiores a 10.',
        )
        self.assertContains(response, '<dialog class="capture-modal"')

# Create your tests here.
