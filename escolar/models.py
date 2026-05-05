from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Semestre(models.Model):
    nombre = models.CharField(max_length=50)
    anio = models.IntegerField()
    periodo = models.CharField(max_length=20, choices=[('ENE-JUN', 'Enero-Junio'), ('AGO-DIC', 'Agosto-Diciembre')])

    def __str__(self):
        return f"{self.nombre} {self.anio}"

    class Meta:
        verbose_name_plural = "Semestres"


class Grupo(models.Model):
    nombre = models.CharField(max_length=10)
    semestre = models.ForeignKey(Semestre, on_delete=models.CASCADE, related_name='grupos')

    def __str__(self):
        return f"Grupo {self.nombre} - {self.semestre}"

    class Meta:
        verbose_name_plural = "Grupos"


class Alumno(models.Model):
    numero_cuenta = models.CharField(max_length=20, unique=True)
    nombre_completo = models.CharField(max_length=150)
    email = models.EmailField()
    grupo = models.ForeignKey(Grupo, on_delete=models.SET_NULL, null=True, related_name='alumnos')

    def __str__(self):
        return f"{self.numero_cuenta} - {self.nombre_completo}"

    class Meta:
        verbose_name_plural = "Alumnos"


class Asignatura(models.Model):
    nombre = models.CharField(max_length=100)
    clave = models.CharField(max_length=20, unique=True)
    semestre = models.ForeignKey(Semestre, on_delete=models.CASCADE, related_name='asignaturas')

    def __str__(self):
        return f"{self.clave} - {self.nombre}"

    class Meta:
        verbose_name_plural = "Asignaturas"


class Calificacion(models.Model):
    PARCIAL_CHOICES = [(1, 'Primer Parcial'), (2, 'Segundo Parcial'), (3, 'Tercer Parcial')]

    alumno = models.ForeignKey(Alumno, on_delete=models.CASCADE, related_name='calificaciones')
    asignatura = models.ForeignKey(Asignatura, on_delete=models.CASCADE, related_name='calificaciones')
    parcial = models.IntegerField(choices=PARCIAL_CHOICES)
    heteroevaluacion = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    coevaluacion = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    autoevaluacion = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )

    @property
    def promedio(self):
        return round((float(self.heteroevaluacion) + float(self.coevaluacion) + float(self.autoevaluacion)) / 3, 2)

    @property
    def aprobado(self):
        return self.promedio >= 6.0

    def __str__(self):
        return f"{self.alumno} - {self.asignatura} - P{self.parcial}"

    class Meta:
        unique_together = ('alumno', 'asignatura', 'parcial')
        verbose_name_plural = "Calificaciones"
