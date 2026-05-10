from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('alumnos/', views.lista_alumnos, name='lista_alumnos'),
    path('calificaciones/', views.calificaciones_grupo, name='calificaciones'),
    path('indicadores/', views.indicadores, name='indicadores'),
    path('pronostico/', views.pronostico_ia, name='pronostico'),
    path('exportar/', views.exportar_excel, name='exportar_excel'),
    path('exportar-pronostico/', views.exportar_pronostico_excel, name='exportar_pronostico'),
    # Profesor
    path('profesor/', views.profesor_panel, name='profesor'),
    path('profesor/importar/', views.importar_excel, name='importar_excel'),
    path('profesor/crear-estructura/', views.crear_estructura, name='crear_estructura'),
    path('profesor/exportar-asignatura/', views.exportar_asignatura, name='exportar_asignatura'),
    path('profesor/plantilla/', views.descargar_plantilla, name='descargar_plantilla'),
    # Captura rápida
    path('captura/', views.captura_rapida, name='captura_rapida'),
    path('captura/guardar/', views.guardar_calificaciones, name='guardar_calificaciones'),
    # Seguimiento
    path('seguimiento/', views.seguimiento, name='seguimiento'),
    path('seguimiento/notas/', views.seguimiento_notas_api, name='seguimiento_notas_api'),
    path('seguimiento/agregar-nota/', views.agregar_nota_api, name='agregar_nota_api'),
]
