# Sistema de Trayectoria Escolar

## Stack Tecnológico
- **Backend**: Django 4.x (Python)
- **Base de datos**: SQLite (incluida) / compatible con PostgreSQL y MySQL
- **Gráficas**: Matplotlib + NumPy
- **Excel**: OpenPyXL
- **IA / Pronóstico**: NumPy (regresión lineal)
- **Frontend**: HTML5 + CSS3 + JavaScript vanilla

## Características del Sistema

### Módulos Incluidos
1. **Dashboard** — Vista general con estadísticas globales
2. **Lista de Alumnos** — Por grupo, semestre y grupo con número de cuenta, nombre y email
3. **Calificaciones** — Por asignatura y por parcial (Hetero, Co y Autoevaluación + promedio)
4. **Indicadores** — Promedio de grupo, asignatura, alumno; índices de aprobación/reprobación con gráficas
5. **Pronóstico IA** — Predicción de P3 usando regresión lineal, alumnos en riesgo
6. **Exportar Excel** — Descarga con calificaciones completas y lista original formateada
7. **Agente IA (Ollama)** — Chat con el modelo local `gemma:4b` que usa como contexto las calificaciones del grupo/alumno seleccionado
8. **Privacidad y consentimiento** — Aviso público integral y simplificado con versión/fecha; registro auditable y revocable del consentimiento de madre, padre o tutor; bloqueo por alumno sin consentimiento vigente
9. **Derechos ARCO** — Recepción pública de solicitudes, panel de coordinación, verificación de identidad, exportaciones JSON/Excel, rectificación, bloqueo y supresión con auditoría

## Instalación

### 1. Crear entorno virtual
```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows
```

### 2. Instalar dependencias
```bash
pip install django openpyxl matplotlib numpy pandas scikit-learn
```

### 3. Aplicar migraciones
```bash
python manage.py migrate
```

### 4. Cargar datos de ejemplo (opcional)
```bash
python manage.py seed_data
```

### 5. Crear superusuario
```bash
python manage.py createsuperuser
```

### 6. Iniciar sesión
El sistema solicita autenticación para acceder a sus módulos. Inicia sesión con el usuario creado
con `createsuperuser`, o crea cuentas adicionales desde `/admin/`. El aviso público está disponible
en `/aviso-de-privacidad/`; completa los datos institucionales señalados antes de usar datos reales.
Después de iniciar sesión, cada usuario debe aceptar la versión vigente del aviso para entrar; si la
rechaza, la sesión se cierra. Las aceptaciones quedan registradas por usuario, versión y fecha/hora, y
se pueden consultar desde el panel de administración.

El personal autorizado registra los consentimientos de alumnos en `/consentimientos/`. El registro
guarda el alumno, la persona tutora y su parentesco, la versión del aviso, la fecha/hora, el medio y
el estado; también permite revocarlo. Por defecto (`REQUIRE_STUDENT_CONSENT = True`), los módulos,
reportes, exportaciones y APIs solo incluyen alumnos con consentimiento vigente para la versión actual.
El panel de consentimientos permite dar de alta el registro necesario para habilitar esos datos.
Para desactivar el bloqueo en una instalación, cambia esa opción a `False` en `settings.py`.
La importación puede registrar alumnos nuevos para iniciar la recolección, pero no guarda sus
calificaciones hasta que tengan consentimiento vigente; después se puede repetir la importación.

### Solicitudes ARCO

Las personas titulares o sus representantes pueden presentar una solicitud en `/derechos-arco/`.
El formulario no revela si existe el número de cuenta indicado. El personal marcado como `is_staff`
tramita los folios desde `/coordinacion/arco/`; antes de exportar o modificar datos debe verificar
la identidad y vincular al titular. El plazo se calcula con `ARCO_RESPONSE_DAYS` (20 días naturales
por defecto); la institución debe configurar y validar el plazo legal aplicable.

Las exportaciones de acceso entregan JSON o Excel con los datos del titular, calificaciones,
seguimiento y consentimientos. Las rectificaciones permitidas se limitan a nombre, número de cuenta
y correo. El bloqueo impide el uso de datos en la plataforma. La supresión requiere escribir
`SUPRIMIR`: elimina calificaciones y notas, anonimiza los identificadores del alumno, desvincula y
anonimiza solicitudes y consentimientos relacionados, y conserva el folio y la bitácora sin datos
identificables. Los eventos de trámite son de solo lectura en administración.

### 7. Iniciar servidor
```bash
python manage.py runserver
```

Visita: http://127.0.0.1:8000

## Estructura del Proyecto
```
trayectoria_escolar/
├── escolar/                    # App principal
│   ├── models.py               # Semestre, Grupo, Alumno, Asignatura, Calificacion
│   ├── views.py                # Lógica de negocio + gráficas
│   ├── urls.py                 # Rutas
│   ├── admin.py                # Panel de administración
│   └── management/commands/    # seed_data
├── templates/escolar/          # Templates HTML
│   ├── base.html               # Layout con sidebar
│   ├── dashboard.html
│   ├── lista_alumnos.html
│   ├── calificaciones.html
│   ├── indicadores.html
│   └── pronostico.html
├── static/css/                 # Estilos
│   └── estilos.css
└── manage.py
```

## Flujo de Trabajo

1. **Admin** → Crear Semestre, Grupos, Asignaturas y Alumnos
2. **Calificaciones** → Registrar notas parciales por alumno y asignatura
3. **Indicadores** → Ver promedios, índices de aprobación/reprobación y gráficas
4. **Pronóstico IA** → Identificar alumnos en riesgo antes del cierre
5. **Exportar Excel** → Descargar reporte completo con formato profesional

## Panel de Administración
URL: http://127.0.0.1:8000/admin/
Accede con una cuenta de superusuario creada mediante `python manage.py createsuperuser`.

## Modelo de Datos

### Consentimiento
- Titular: alumno; madre, padre o tutor legal que otorga el consentimiento
- Versión del aviso, fecha/hora de registro, medio de obtención y estado vigente/revocado
- Una nueva aceptación revoca el registro vigente anterior; la revocación conserva la evidencia histórica

### Calificación
- **Heteroevaluación**: evaluación del docente (0–10)
- **Coevaluación**: evaluación entre pares (0–10)
- **Autoevaluación**: evaluación del propio alumno (0–10)
- **Promedio**: (hetero + co + auto) / 3

### Criterio de aprobación
- Promedio ≥ 6.0 → Aprobado
- Promedio < 6.0 → Reprobado

## Pronóstico IA
Utiliza **regresión lineal** (NumPy `polyfit`) sobre los promedios de los parciales 1 y 2 para predecir el resultado del Parcial 3. Clasifica la tendencia como:
- 📈 **Mejora**: pendiente > 0.2
- ➡️ **Estable**: pendiente entre -0.2 y 0.2
- 📉 **Riesgo alto**: pendiente < -0.2


## Agente IA con Ollama (gemma:4b)
Ruta: `/agente/` (menú lateral → Análisis → *Agente IA*).

1. Instalar [Ollama](https://ollama.com) y descargar el modelo:
```bash
ollama pull gemma:4b
ollama serve        # si no corre ya como servicio
```
2. Iniciar el proyecto normalmente (`python manage.py runserver`). No requiere dependencias nuevas.

Configuración opcional por variables de entorno (ver `settings.py`):
| Variable | Valor por defecto |
|---|---|
| `OLLAMA_MODEL` | `gemma:4b` |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` |
| `OLLAMA_TIMEOUT` | `300` (segundos) |

Si el nombre de tu modelo en Ollama es distinto (p. ej. `gemma3:4b`), ejecuta `ollama list` y define `OLLAMA_MODEL` con ese nombre.

Archivos: `escolar/ollama_client.py` (cliente HTTP con streaming), `escolar/agente.py` (prompt + contexto desde la BD), vistas `agente_*` en `views.py` y `templates/escolar/agente.html`.
