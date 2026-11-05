import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get('DJANGO_DEBUG', 'true').lower() in {'1', 'true', 'yes'}
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Define DJANGO_SECRET_KEY cuando DJANGO_DEBUG esté desactivado.')
    SECRET_KEY = 'techthree-development-only-key-never-use-in-production-2026'

configured_hosts = os.environ.get('DJANGO_ALLOWED_HOSTS')
ALLOWED_HOSTS = (
    [host.strip() for host in configured_hosts.split(',') if host.strip()]
    if configured_hosts
    else (['*'] if DEBUG else ['localhost', '127.0.0.1'])
)
SECURE_HSTS_SECONDS = int(
    os.environ.get('DJANGO_SECURE_HSTS_SECONDS', '31536000' if not DEBUG else '0'),
)
SECURE_SSL_REDIRECT = os.environ.get(
    'DJANGO_SECURE_SSL_REDIRECT', str(not DEBUG),
).lower() in {'1', 'true', 'yes'}
SESSION_COOKIE_SECURE = os.environ.get(
    'DJANGO_SESSION_COOKIE_SECURE', str(not DEBUG),
).lower() in {'1', 'true', 'yes'}
CSRF_COOKIE_SECURE = os.environ.get(
    'DJANGO_CSRF_COOKIE_SECURE', str(not DEBUG),
).lower() in {'1', 'true', 'yes'}

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'escolar',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'escolar.middleware.LoginRequiredMiddleware',
]

ROOT_URLCONF = 'trayectoria_escolar.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'trayectoria_escolar.wsgi.application'
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Mexico_City'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_LOCKOUT_DURATION = 15 * 60
PRIVACY_NOTICE_VERSION = '2026-10-26'
PRIVACY_NOTICE_DATE = '26 de octubre de 2026'
REQUIRE_STUDENT_CONSENT = True
ARCO_RESPONSE_DAYS = 20

# ── Agente IA (Ollama) ────────────────────────────────────────────────────────
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'gemma3:4b ')
OLLAMA_TIMEOUT = int(os.environ.get('OLLAMA_TIMEOUT', '300'))
