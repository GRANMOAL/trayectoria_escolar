from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.utils.deprecation import MiddlewareMixin

from .models import AvisoPrivacidadAceptacion
from .privacidad import PRIVACY_NOTICE_VERSION


class LoginRequiredMiddleware(MiddlewareMixin):
    """Require authentication and current privacy notice acceptance."""

    public_url_names = {
        'login',
        'aviso_privacidad',
        'solicitud_arco',
        'solicitar_revision_pronostico',
    }

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if request.path_info.startswith(settings.STATIC_URL):
            return None

        if not request.user.is_authenticated:
            if match and match.url_name in self.public_url_names:
                return None
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

        if match and match.url_name in {
            'login',
            'logout',
            'aviso_privacidad',
            'privacy_acceptance',
            'solicitud_arco',
            'solicitar_revision_pronostico',
        }:
            return None

        accepted = AvisoPrivacidadAceptacion.objects.filter(
            usuario=request.user,
            version=PRIVACY_NOTICE_VERSION,
        ).exists()
        if not accepted:
            return redirect_to_login(request.get_full_path(), '/aviso-de-privacidad/aceptar/')
        return None
