import random
from django.core.management.base import BaseCommand
from escolar.models import Semestre, Grupo, Alumno, Asignatura, Calificacion


class Command(BaseCommand):
    help = 'Carga datos de ejemplo'

    def handle(self, *args, **kwargs):
        # Semestre
        sem, _ = Semestre.objects.get_or_create(nombre='Semestre 2024-A', anio=2024, periodo='ENE-JUN')

        # Grupos
        grupo_a, _ = Grupo.objects.get_or_create(nombre='A', semestre=sem)
        grupo_b, _ = Grupo.objects.get_or_create(nombre='B', semestre=sem)

        # Asignaturas
        asignaturas_data = [
            ('MAT101', 'Matemáticas I'), ('INF201', 'Programación'),
            ('FIS101', 'Física I'), ('ING301', 'Inglés'), ('ADM101', 'Administración'),
        ]
        asignaturas = []
        for clave, nombre in asignaturas_data:
            a, _ = Asignatura.objects.get_or_create(clave=clave, defaults={'nombre': nombre, 'semestre': sem})
            asignaturas.append(a)

        # Alumnos grupo A
        alumnos_a = [
            ('2024001', 'García López María', 'mgarcia@ithi.edu.mx'),
            ('2024002', 'Hernández Ramírez Juan', 'jhernandez@ithi.edu.mx'),
            ('2024003', 'Martínez Torres Ana', 'amartinez@ithi.edu.mx'),
            ('2024004', 'López Sánchez Carlos', 'clopez@ithi.edu.mx'),
            ('2024005', 'Rodríguez Pérez Laura', 'lrodriguez@ithi.edu.mx'),
            ('2024006', 'González Cruz Diego', 'dgonzalez@ithi.edu.mx'),
            ('2024007', 'Flores Jiménez Sara', 'sflores@ithi.edu.mx'),
            ('2024008', 'Morales Reyes Pedro', 'pmorales@ithi.edu.mx'),
        ]
        alumnos_b = [
            ('2024009', 'Vásquez Mendoza Sofía', 'svasquez@ithi.edu.mx'),
            ('2024010', 'Castillo Rivera Miguel', 'mcastillo@ithi.edu.mx'),
            ('2024011', 'Ortiz Vargas Daniela', 'dortiz@ithi.edu.mx'),
            ('2024012', 'Ruiz Contreras Andrés', 'aruiz@ithi.edu.mx'),
            ('2024013', 'Aguilar Espinosa Valentina', 'vaguilar@ithi.edu.mx'),
            ('2024014', 'Torres Guerrero Emilio', 'etorres@ithi.edu.mx'),
        ]

        all_alumnos = []
        for nc, nombre, email in alumnos_a:
            al, _ = Alumno.objects.get_or_create(numero_cuenta=nc, defaults={'nombre_completo': nombre, 'email': email, 'grupo': grupo_a})
            all_alumnos.append(al)
        for nc, nombre, email in alumnos_b:
            al, _ = Alumno.objects.get_or_create(numero_cuenta=nc, defaults={'nombre_completo': nombre, 'email': email, 'grupo': grupo_b})
            all_alumnos.append(al)

        # Calificaciones
        random.seed(42)
        for alumno in all_alumnos:
            for asig in asignaturas:
                for parcial in [1, 2, 3]:
                    if not Calificacion.objects.filter(alumno=alumno, asignatura=asig, parcial=parcial).exists():
                        # Algunos alumnos tienen tendencia a reprobar
                        base = random.uniform(5.0, 9.5) if random.random() > 0.2 else random.uniform(3.0, 6.5)
                        hetero = round(min(10, max(0, base + random.uniform(-1, 1))), 2)
                        co = round(min(10, max(0, base + random.uniform(-1, 1))), 2)
                        auto = round(min(10, max(0, base + random.uniform(-0.5, 0.5))), 2)
                        Calificacion.objects.create(
                            alumno=alumno, asignatura=asig, parcial=parcial,
                            heteroevaluacion=hetero, coevaluacion=co, autoevaluacion=auto
                        )

        self.stdout.write(self.style.SUCCESS('✓ Datos de ejemplo cargados exitosamente'))
