from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, include
from django.views.generic.base import RedirectView
from escolar import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        'login/<path:unknown_path>',
        RedirectView.as_view(url='/login/', permanent=False),
        name='login_fallback',
    ),
    path('login/', views.LoginRequiringPrivacyAcceptanceView.as_view(
        template_name='registration/login.html',
        redirect_authenticated_user=True,
    ), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('aviso-de-privacidad/', views.aviso_privacidad, name='aviso_privacidad'),
    path(
        'aviso-de-privacidad/aceptar/',
        views.aceptar_aviso_privacidad,
        name='privacy_acceptance',
    ),
    path('', include('escolar.urls')),
]
