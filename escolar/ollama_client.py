"""Cliente mínimo para la API HTTP de Ollama."""
import json
import urllib.request
import urllib.error

from django.conf import settings


class OllamaError(Exception):
    pass


def _url(path):
    return settings.OLLAMA_HOST.rstrip('/') + path


def estado():
    modelo = settings.OLLAMA_MODEL
    try:
        with urllib.request.urlopen(_url('/api/tags'), timeout=5) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return {'servidor': False, 'modelo': modelo, 'modelo_instalado': False,
                'instalados': [], 'error': str(e)}
    instalados = [m.get('name', '') for m in data.get('models', [])]
    ok = any(n == modelo or n.split(':')[0] == modelo or n == f'{modelo}:latest' for n in instalados)
    return {'servidor': True, 'modelo': modelo, 'modelo_instalado': ok, 'instalados': instalados}


def chat_stream(mensajes):
    """Generador de fragmentos"""
    payload = json.dumps({
        'model': settings.OLLAMA_MODEL,
        'messages': mensajes,
        'stream': True,
        'options': {'temperature': 0.3},
    }).encode()
    req = urllib.request.Request(_url('/api/chat'), data=payload,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=settings.OLLAMA_TIMEOUT) as r:
            for linea in r:
                if not linea.strip():
                    continue
                d = json.loads(linea.decode())
                if d.get('error'):
                    raise OllamaError(d['error'])
                trozo = d.get('message', {}).get('content', '')
                if trozo:
                    yield trozo
                if d.get('done'):
                    break
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode(errors='ignore')
        if e.code == 404:
            raise OllamaError(
                f"El modelo '{settings.OLLAMA_MODEL}' no está instalado en Ollama. "
                f"Ejecuta: ollama pull {settings.OLLAMA_MODEL}")
        raise OllamaError(f'Ollama respondió {e.code}: {cuerpo}')
    except urllib.error.URLError as e:
        raise OllamaError(
            f'No se pudo conectar con Ollama en {settings.OLLAMA_HOST}. '
            f'¿Está corriendo "ollama serve"? ({e.reason})')
