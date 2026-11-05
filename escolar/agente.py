"""Agente académico"""

from .models import Grupo, Alumno, Asignatura, Calificacion, Nota
from .ollama_client import chat_stream
from .privacidad import alumnos_con_consentimiento
from .pronosticos import explicar_pronostico

MINIMO = 7.0

SYSTEM_PROMPT = (
    "Eres el asistente académico del Sistema de Trayectoria Escolar de una preparatoria. "
    "Ayudas a tutores y profesores a interpretar calificaciones, detectar alumnos en riesgo "
    "y proponer acciones de seguimiento. Responde siempre en español, de forma clara y concisa. "
    f"La calificación mínima aprobatoria es {MINIMO}. Cada calificación es el promedio de "
    "heteroevaluación, coevaluación y autoevaluación. "
    "Usa ÚNICAMENTE los datos del contexto proporcionado; si falta información, dilo y no inventes cifras. "
    "Trata los datos de los alumnos con confidencialidad y sin juicios personales."
)


def contexto_grupo(grupo):
    asignaturas = list(Asignatura.objects.filter(semestre=grupo.semestre))
    alumnos = list(alumnos_con_consentimiento().filter(grupo=grupo).order_by('nombre_completo'))
    califs = list(Calificacion.objects.filter(alumno__in=alumnos))
    lineas = [f"GRUPO: {grupo} — {len(alumnos)} alumnos."]

    lineas.append("Promedio por asignatura y parcial:")
    lineas.extend(_lineas_promedio_asignatura(asignaturas, califs))

    lineas.append("Alumnos (promedios por parcial; pronóstico P3 por regresión lineal; * = en riesgo):")
    calificaciones_por_alumno = {}
    for calificacion in califs:
        calificaciones_por_alumno.setdefault(calificacion.alumno_id, []).append(calificacion)
    for al in alumnos:
        lineas.append(_linea_contexto_alumno(
            al, asignaturas, calificaciones_por_alumno.get(al.id, []),
        ))
    return "\n".join(lineas)


def _lineas_promedio_asignatura(asignaturas, calificaciones):
    por_asignatura_parcial = {}
    for calificacion in calificaciones:
        clave = (calificacion.asignatura_id, calificacion.parcial)
        por_asignatura_parcial.setdefault(clave, []).append(calificacion.promedio)
    lineas = []
    for asignatura in asignaturas:
        partes = []
        for parcial in (1, 2, 3):
            notas = por_asignatura_parcial.get((asignatura.id, parcial), [])
            if notas:
                partes.append(f"P{parcial}={sum(notas) / len(notas):.2f}")
        if partes:
            lineas.append(f"- {asignatura.nombre}: " + ", ".join(partes))
    return lineas


def _linea_contexto_alumno(alumno, asignaturas, calificaciones):
    if not calificaciones:
        return f"- {alumno.nombre_completo} ({alumno.numero_cuenta}): sin calificaciones"
    promedios_parcial = _promedios_parciales_alumno(calificaciones)
    riesgos = _asignaturas_en_riesgo(asignaturas, calificaciones)
    texto = ", ".join(
        f"P{parcial}={promedio}" for parcial, promedio in promedios_parcial.items()
    )
    riesgo = f" * RIESGO en: {'; '.join(riesgos)}" if riesgos else ""
    return f"- {alumno.nombre_completo} ({alumno.numero_cuenta}): {texto}{riesgo}"


def _promedios_parciales_alumno(calificaciones):
    promedios = {}
    for parcial in (1, 2, 3):
        notas = [
            calificacion.promedio for calificacion in calificaciones
            if calificacion.parcial == parcial
        ]
        if notas:
            promedios[parcial] = round(sum(notas) / len(notas), 2)
    return promedios


def _asignaturas_en_riesgo(asignaturas, calificaciones):
    por_asignatura = {}
    for calificacion in calificaciones:
        por_asignatura.setdefault(calificacion.asignatura_id, {})[
            calificacion.parcial
        ] = calificacion.promedio
    riesgos = []
    for asignatura in asignaturas:
        promedios = por_asignatura.get(asignatura.id, {})
        estimacion = explicar_pronostico(promedios)
        pronostico = estimacion['pronostico'] if estimacion else None
        ultimo_parcial = max(promedios) if promedios else None
        referencia = pronostico if pronostico is not None else promedios.get(ultimo_parcial)
        if referencia is not None and referencia < MINIMO:
            riesgos.append(f"{asignatura.nombre} ({referencia})")
    return riesgos


def contexto_alumno(alumno):
    lineas = [f"ALUMNO: {alumno.nombre_completo} ({alumno.numero_cuenta}), {alumno.grupo or 'sin grupo'}."]
    cs = list(alumno.calificaciones.select_related('asignatura').order_by('asignatura__nombre', 'parcial'))
    for c in cs:
        lineas.append(
            f"- {c.asignatura.nombre} P{c.parcial}: hetero={c.heteroevaluacion}, "
            f"co={c.coevaluacion}, auto={c.autoevaluacion}, promedio={c.promedio}")
    notas = list(Nota.objects.filter(alumno=alumno)[:10])
    if notas:
        lineas.append("Notas del tutor (recientes):")
        for n in notas:
            lineas.append(f"- [{n.tipo}] {n.fecha:%d/%m/%Y}: {n.texto}")
    return "\n".join(lineas)


def construir_mensajes(historial, grupo_id=None, alumno_id=None):
    contexto = []
    if grupo_id:
        g = Grupo.objects.select_related('semestre').filter(id=grupo_id).first()
        if g:
            contexto.append(contexto_grupo(g))
    if alumno_id:
        a = alumnos_con_consentimiento().select_related('grupo').filter(id=alumno_id).first()
        if a:
            contexto.append(contexto_alumno(a))
    sistema = SYSTEM_PROMPT
    if contexto:
        sistema += "\n\n=== CONTEXTO DEL SISTEMA ===\n" + "\n\n".join(contexto)
    else:
        sistema += "\n\n(No se seleccionó grupo ni alumno; responde de forma general.)"
    msgs = [{'role': 'system', 'content': sistema}]
    for m in historial[-12:]:
        if m.get('role') in ('user', 'assistant') and str(m.get('content', '')).strip():
            msgs.append({'role': m['role'], 'content': str(m['content'])})
    return msgs


def responder_stream(historial, grupo_id=None, alumno_id=None):
    return chat_stream(construir_mensajes(historial, grupo_id, alumno_id))
