from django.contrib import admin
from .privacidad import alumnos_con_consentimiento, consentimiento_requerido
from .models import (
    Semestre, Grupo, Alumno, Asignatura, Calificacion, Nota,
    AvisoPrivacidadAceptacion, Consentimiento, SolicitudARCO,
    SolicitudARCOEvento,
)

admin.site.site_header = "Sistema de Trayectoria Escolar"
admin.site.site_title = "Trayectoria Escolar"
admin.site.index_title = "Administración del Sistema"

@admin.register(Semestre)
class SemestreAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'anio', 'periodo']

@admin.register(Grupo)
class GrupoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'semestre']
    list_filter = ['semestre']

@admin.register(Alumno)
class AlumnoAdmin(admin.ModelAdmin):
    list_display = ['numero_cuenta', 'nombre_completo', 'email', 'grupo']
    list_filter = ['grupo__semestre', 'grupo']
    search_fields = ['numero_cuenta', 'nombre_completo', 'email']

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if consentimiento_requerido():
            queryset = queryset.filter(pk__in=alumnos_con_consentimiento())
        return queryset

@admin.register(Asignatura)
class AsignaturaAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'semestre']
    list_filter = ['semestre']

@admin.register(Calificacion)
class CalificacionAdmin(admin.ModelAdmin):
    list_display = ['alumno', 'asignatura', 'parcial', 'heteroevaluacion', 'coevaluacion', 'autoevaluacion']
    list_filter = ['parcial', 'asignatura', 'alumno__grupo']
    search_fields = ['alumno__nombre_completo', 'alumno__numero_cuenta']

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if consentimiento_requerido():
            queryset = queryset.filter(alumno__in=alumnos_con_consentimiento())
        return queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'alumno' and consentimiento_requerido():
            kwargs['queryset'] = alumnos_con_consentimiento()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Nota)
class NotaAdmin(admin.ModelAdmin):
    list_display = ['alumno', 'tipo', 'fecha']
    list_filter = ['tipo', 'fecha']
    search_fields = ['alumno__nombre_completo', 'alumno__numero_cuenta', 'texto']
    readonly_fields = ['fecha']

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if consentimiento_requerido():
            queryset = queryset.filter(alumno__in=alumnos_con_consentimiento())
        return queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'alumno' and consentimiento_requerido():
            kwargs['queryset'] = alumnos_con_consentimiento()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(AvisoPrivacidadAceptacion)
class AvisoPrivacidadAceptacionAdmin(admin.ModelAdmin):
    list_display = ['usuario', 'version', 'aceptado_en']
    list_filter = ['version', 'aceptado_en']
    search_fields = ['usuario__username']
    readonly_fields = ['usuario', 'version', 'aceptado_en']


@admin.register(Consentimiento)
class ConsentimientoAdmin(admin.ModelAdmin):
    list_display = ['alumno', 'tutor_nombre', 'parentesco', 'version_aviso', 'fecha_hora', 'medio', 'estado']
    list_filter = ['estado', 'parentesco', 'version_aviso', 'medio']
    search_fields = ['alumno__nombre_completo', 'alumno__numero_cuenta', 'tutor_nombre']
    readonly_fields = [
        'alumno', 'tutor_nombre', 'parentesco', 'version_aviso', 'fecha_hora',
        'medio', 'estado', 'revocado_en', 'registrado_por',
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SolicitudARCO)
class SolicitudARCOAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'tipo', 'titular_nombre', 'estado', 'fecha_solicitud',
        'fecha_limite', 'responsable',
    ]
    list_filter = ['tipo', 'estado', 'fecha_limite']
    search_fields = ['titular_nombre', 'numero_cuenta', 'solicitante', 'contacto_email']
    readonly_fields = [
        'tipo', 'titular', 'titular_nombre', 'numero_cuenta', 'solicitante',
        'parentesco', 'contacto_email', 'descripcion', 'estado', 'fecha_solicitud',
        'fecha_limite', 'fecha_respuesta', 'responsable', 'respuesta',
        'identidad_verificada', 'fecha_verificacion',
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SolicitudARCOEvento)
class SolicitudARCOEventoAdmin(admin.ModelAdmin):
    list_display = ['solicitud', 'accion', 'actor', 'fecha_hora']
    list_filter = ['accion', 'fecha_hora']
    search_fields = ['solicitud__id', 'detalle']
    readonly_fields = ['solicitud', 'actor', 'fecha_hora', 'accion', 'detalle']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
