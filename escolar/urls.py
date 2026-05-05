from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('alumnos/', views.lista_alumnos, name='lista_alumnos'),
    path('calificaciones/', views.calificaciones_grupo, name='calificaciones'),
    path('indicadores/', views.indicadores, name='indicadores'),
    path('pronostico/', views.pronostico_ia, name='pronostico'),
    path('exportar/', views.exportar_excel, name='exportar_excel'),
]
