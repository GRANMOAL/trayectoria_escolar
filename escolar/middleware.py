from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.urls import reverse


class AutenticacionRequeridaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        public_paths = {
            reverse('login'),
            reverse('registro'),
        }
        is_static = request.path_info.startswith(settings.STATIC_URL)

        if (
            not request.user.is_authenticated
            and request.path_info not in public_paths
            and not is_static
        ):
            return redirect_to_login(request.get_full_path(), reverse('login'))

        return self.get_response(request)
