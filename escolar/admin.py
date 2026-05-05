from django.contrib import admin
from .models import Semestre, Grupo, Alumno, Asignatura, Calificacion

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

@admin.register(Asignatura)
class AsignaturaAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'semestre']
    list_filter = ['semestre']

@admin.register(Calificacion)
class CalificacionAdmin(admin.ModelAdmin):
    list_display = ['alumno', 'asignatura', 'parcial', 'heteroevaluacion', 'coevaluacion', 'autoevaluacion']
    list_filter = ['parcial', 'asignatura', 'alumno__grupo']
    search_fields = ['alumno__nombre_completo', 'alumno__numero_cuenta']
