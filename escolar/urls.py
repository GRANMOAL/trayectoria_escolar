from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('alumnos/', views.lista_alumnos, name='lista_alumnos'),
    path('calificaciones/', views.calificaciones_grupo, name='calificaciones'),
    path('indicadores/', views.indicadores, name='indicadores'),
    path('pronostico/', views.pronostico_ia, name='pronostico'),
    path(
        'pronostico/solicitar-revision/',
        views.solicitar_revision_pronostico,
        name='solicitar_revision_pronostico',
    ),
    path(
        'coordinacion/pronosticos/',
        views.coordinacion_pronosticos,
        name='coordinacion_pronosticos',
    ),
    path('exportar/', views.exportar_excel, name='exportar_excel'),
    path('exportar-pronostico/', views.exportar_pronostico_excel, name='exportar_pronostico'),
    path('exportar-resumen-indicadores/', views.exportar_resumen_indicadores, name='exportar_resumen_indicadores'),
    path('exportar-indices-pronostico/', views.exportar_indices_pronostico, name='exportar_indices_pronostico'),
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
    path('consentimientos/', views.gestionar_consentimientos, name='gestionar_consentimientos'),
    path(
        'consentimientos/<int:consentimiento_id>/revocar/',
        views.revocar_consentimiento,
        name='revocar_consentimiento',
    ),
    path('derechos-arco/', views.solicitud_arco, name='solicitud_arco'),
    path('coordinacion/arco/', views.coordinacion_arco, name='coordinacion_arco'),
    # Agente IA (Ollama)
    path('agente/', views.agente_ia, name='agente_ia'),
    path('agente/chat/', views.agente_chat_api, name='agente_chat_api'),
    path('agente/estado/', views.agente_estado_api, name='agente_estado_api'),
    path('agente/alumnos/', views.agente_alumnos_api, name='agente_alumnos_api'),
]
