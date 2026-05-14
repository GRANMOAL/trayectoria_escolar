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

### 6. Iniciar servidor
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

## Imagenes del Proyecto
1. Dashboard
<img width="2556" height="1171" alt="image" src="https://github.com/user-attachments/assets/3468a80b-4325-44ee-8d32-f7c60010e212" />

2. Lista de Alumnos
<img width="2559" height="1172" alt="image" src="https://github.com/user-attachments/assets/dca33dc9-fd9a-4ea6-b457-6cc3d040545d" />

4. Calificaciones
<img width="2559" height="1169" alt="image" src="https://github.com/user-attachments/assets/4fb187a4-af10-466b-9aee-a40a181e0706" />

6. Indicadores
<img width="2557" height="1165" alt="image" src="https://github.com/user-attachments/assets/ec18dea6-89ae-4b08-84b4-9ef1858d2deb" />

8. Pronostico IA
<img width="2556" height="1155" alt="image" src="https://github.com/user-attachments/assets/70c7e6a0-f76b-4252-a109-3f254690a05e" />

10. Captura rapida
<img width="2557" height="1169" alt="image" src="https://github.com/user-attachments/assets/32757236-803e-42e1-a92f-0866f40e60ae" />

12. Seguimiento
<img width="2558" height="1159" alt="image" src="https://github.com/user-attachments/assets/13dbf300-368f-4b30-8d98-4cad7cff9041" />

14. Panel Tutor
<img width="2540" height="1144" alt="image" src="https://github.com/user-attachments/assets/7c480bf0-c51b-440b-96a3-99b7b24b52db" />


## Flujo de Trabajo

1. **Admin** → Crear Semestre, Grupos, Asignaturas y Alumnos
2. **Calificaciones** → Registrar notas parciales por alumno y asignatura
3. **Indicadores** → Ver promedios, índices de aprobación/reprobación y gráficas
4. **Pronóstico IA** → Identificar alumnos en riesgo antes del cierre
5. **Exportar Excel** → Descargar reporte completo con formato profesional

## Panel de Administración
URL: http://127.0.0.1:8000/admin/
- Usuario: `admin`
- Contraseña: `admin123` (cambiar en producción)

## Modelo de Datos

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
