from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import Q
from django.utils import timezone


class Semestre(models.Model):
    nombre = models.CharField(max_length=50)
    anio = models.IntegerField()
    periodo = models.CharField(max_length=20, choices=[('ENE-JUN', 'Enero-Junio'), ('AGO-DIC', 'Agosto-Diciembre')])

    def __str__(self):
        return f"{self.nombre} {self.anio}"

    class Meta:
        verbose_name_plural = "Semestres"
        ordering = ['-anio']


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
    email = models.EmailField(blank=True, default='')
    grupo = models.ForeignKey(Grupo, on_delete=models.SET_NULL, null=True, related_name='alumnos')
    fecha_registro = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    bloqueado_arco = models.BooleanField(default=False)
    suprimido_arco = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.numero_cuenta} - {self.nombre_completo}"

    class Meta:
        verbose_name_plural = "Alumnos"
        ordering = ['nombre_completo']


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
    CALIFICACION_VALIDATORS = [
        MinValueValidator(0),
        MaxValueValidator(10),
    ]

    alumno = models.ForeignKey(Alumno, on_delete=models.CASCADE, related_name='calificaciones')
    asignatura = models.ForeignKey(Asignatura, on_delete=models.CASCADE, related_name='calificaciones')
    parcial = models.IntegerField(choices=PARCIAL_CHOICES)
    heteroevaluacion = models.DecimalField(max_digits=5, decimal_places=2, validators=CALIFICACION_VALIDATORS)
    coevaluacion = models.DecimalField(max_digits=5, decimal_places=2, validators=CALIFICACION_VALIDATORS)
    autoevaluacion = models.DecimalField(max_digits=5, decimal_places=2, validators=CALIFICACION_VALIDATORS)
    fecha_captura = models.DateTimeField(auto_now=True, null=True, blank=True)

    @property
    def promedio(self):
        return round((float(self.heteroevaluacion) + float(self.coevaluacion) + float(self.autoevaluacion)) / 3, 2)

    @property
    def aprobado(self):
        return self.promedio >= 7.0

    def __str__(self):
        return f"{self.alumno} - {self.asignatura} - P{self.parcial}"

    class Meta:
        unique_together = ('alumno', 'asignatura', 'parcial')
        verbose_name_plural = "Calificaciones"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(heteroevaluacion__gte=0, heteroevaluacion__lte=10)
                    & Q(coevaluacion__gte=0, coevaluacion__lte=10)
                    & Q(autoevaluacion__gte=0, autoevaluacion__lte=10)
                ),
                name='calificaciones_between_0_and_10',
            ),
        ]


class PronosticoLog(models.Model):
    alumno = models.ForeignKey(
        Alumno,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pronosticos_ia',
    )
    asignatura = models.ForeignKey(
        Asignatura,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pronosticos_ia',
    )
    version_modelo = models.CharField(max_length=40)
    huella_entradas = models.CharField(max_length=64)
    entradas_agregadas = models.JSONField()
    salida = models.JSONField()
    fecha = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-fecha']
        constraints = [
            models.UniqueConstraint(
                fields=['alumno', 'asignatura', 'version_modelo', 'huella_entradas'],
                name='unique_pronostico_log_inputs',
            ),
        ]

    def __str__(self):
        return f"Pronóstico {self.version_modelo} · {self.alumno} · {self.asignatura}"


class SolicitudRevisionPronostico(models.Model):
    ESTADO_CHOICES = [
        ('recibida', 'Recibida'),
        ('en_revision', 'En revisión'),
        ('atendida', 'Atendida'),
    ]
    PARENTESCO_CHOICES = [
        ('alumno', 'Alumno/a'),
        ('madre', 'Madre'),
        ('padre', 'Padre'),
        ('tutor', 'Tutor/a'),
    ]

    alumno = models.ForeignKey(
        Alumno,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_revision_pronostico',
    )
    pronostico = models.ForeignKey(
        PronosticoLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_revision',
    )
    solicitante = models.CharField(max_length=150)
    parentesco = models.CharField(max_length=10, choices=PARENTESCO_CHOICES)
    contacto_email = models.EmailField()
    motivo = models.TextField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='recibida')
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='revisiones_pronostico_responsable',
    )
    respuesta = models.TextField(blank=True)

    class Meta:
        ordering = ['estado', '-fecha_solicitud']

    def __str__(self):
        return f"Revisión de pronóstico #{self.pk or 'nueva'} · {self.get_estado_display()}"


class SolicitudRevisionPronosticoEvento(models.Model):
    solicitud = models.ForeignKey(
        SolicitudRevisionPronostico,
        on_delete=models.CASCADE,
        related_name='eventos',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_revision_pronostico',
    )
    fecha_hora = models.DateTimeField(default=timezone.now, editable=False)
    accion = models.CharField(max_length=40)
    detalle = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha_hora']


class Nota(models.Model):
    """Notas/observaciones del tutor sobre un alumno"""
    alumno = models.ForeignKey(Alumno, on_delete=models.CASCADE, related_name='notas')
    texto = models.TextField()
    fecha = models.DateTimeField(auto_now_add=True)
    tipo = models.CharField(max_length=20, choices=[
        ('observacion', 'Observación'), ('seguimiento', 'Seguimiento'),
        ('alerta', 'Alerta'), ('logro', 'Logro'),
    ], default='observacion')

    class Meta:
        ordering = ['-fecha']


class AvisoPrivacidadAceptacion(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='aceptaciones_privacidad',
    )
    version = models.CharField(max_length=20)
    aceptado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-aceptado_en']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'version'],
                name='unique_privacy_acceptance_per_user_version',
            ),
        ]

    def __str__(self):
        return f"{self.usuario} aceptó el aviso {self.version}"


class Consentimiento(models.Model):
    ESTADO_VIGENTE = 'vigente'
    ESTADO_REVOCADO = 'revocado'
    ESTADO_CHOICES = [
        (ESTADO_VIGENTE, 'Vigente'),
        (ESTADO_REVOCADO, 'Revocado'),
    ]
    PARENTESCO_CHOICES = [
        ('madre', 'Madre'),
        ('padre', 'Padre'),
        ('tutor', 'Tutor/a legal'),
    ]
    MEDIO_CHOICES = [
        ('portal', 'Portal electrónico'),
        ('presencial', 'Presencial'),
        ('documento', 'Documento firmado'),
        ('telefono', 'Teléfono'),
    ]

    alumno = models.ForeignKey(
        Alumno,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consentimientos',
    )
    tutor_nombre = models.CharField(max_length=150, blank=True)
    parentesco = models.CharField(max_length=10, choices=PARENTESCO_CHOICES)
    version_aviso = models.CharField(max_length=20)
    fecha_hora = models.DateTimeField(default=timezone.now, editable=False)
    medio = models.CharField(max_length=20, choices=MEDIO_CHOICES)
    estado = models.CharField(
        max_length=10,
        choices=ESTADO_CHOICES,
        default=ESTADO_VIGENTE,
    )
    revocado_en = models.DateTimeField(null=True, blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consentimientos_registrados',
    )

    class Meta:
        ordering = ['-fecha_hora']
        constraints = [
            models.UniqueConstraint(
                fields=['alumno'],
                condition=Q(estado='vigente'),
                name='one_current_consent_per_student',
            ),
        ]

    def __str__(self):
        return f"Consentimiento de {self.alumno} ({self.version_aviso}): {self.estado}"


class SolicitudARCO(models.Model):
    TIPO_CHOICES = [
        ('acceso', 'Acceso'),
        ('rectificacion', 'Rectificación'),
        ('cancelacion', 'Cancelación'),
        ('oposicion', 'Oposición'),
    ]
    ESTADO_CHOICES = [
        ('recibida', 'Recibida'),
        ('identidad_pendiente', 'Identidad pendiente'),
        ('en_revision', 'En revisión'),
        ('atendida', 'Atendida'),
        ('improcedente', 'Improcedente'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    titular = models.ForeignKey(
        Alumno,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_arco',
    )
    titular_nombre = models.CharField(max_length=150, blank=True)
    numero_cuenta = models.CharField(max_length=20, blank=True)
    solicitante = models.CharField(max_length=150, blank=True)
    parentesco = models.CharField(max_length=30, blank=True)
    contacto_email = models.EmailField(blank=True)
    descripcion = models.TextField(blank=True)
    estado = models.CharField(max_length=24, choices=ESTADO_CHOICES, default='recibida')
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_limite = models.DateField()
    fecha_respuesta = models.DateTimeField(null=True, blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_arco_responsable',
    )
    respuesta = models.TextField(blank=True)
    identidad_verificada = models.BooleanField(default=False)
    fecha_verificacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['fecha_limite', '-fecha_solicitud']

    def __str__(self):
        return f"ARCO-{self.pk or 'nuevo'} · {self.get_tipo_display()} · {self.get_estado_display()}"


class SolicitudARCOEvento(models.Model):
    solicitud = models.ForeignKey(
        SolicitudARCO,
        on_delete=models.CASCADE,
        related_name='eventos',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_arco',
    )
    fecha_hora = models.DateTimeField(default=timezone.now, editable=False)
    accion = models.CharField(max_length=40)
    detalle = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha_hora']

    def __str__(self):
        return f"{self.solicitud} · {self.accion} · {self.fecha_hora:%Y-%m-%d %H:%M}"
