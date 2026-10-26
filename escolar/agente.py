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
    for a in asignaturas:
        partes = []
        for p in (1, 2, 3):
            vals = [c.promedio for c in califs if c.asignatura_id == a.id and c.parcial == p]
            if vals:
                partes.append(f"P{p}={sum(vals)/len(vals):.2f}")
        if partes:
            lineas.append(f"- {a.nombre}: " + ", ".join(partes))

    lineas.append("Alumnos (promedios por parcial; pronóstico P3 por regresión lineal; * = en riesgo):")
    for al in alumnos:
        cs = [c for c in califs if c.alumno_id == al.id]
        if not cs:
            lineas.append(f"- {al.nombre_completo} ({al.numero_cuenta}): sin calificaciones")
            continue
        por_parcial = {}
        for p in (1, 2, 3):
            v = [c.promedio for c in cs if c.parcial == p]
            if v:
                por_parcial[p] = round(sum(v) / len(v), 2)
        reprob = []
        for a in asignaturas:
            prom = {
                c.parcial: c.promedio
                for c in cs
                if c.asignatura_id == a.id
            }
            estimacion = explicar_pronostico(prom)
            pron = estimacion['pronostico'] if estimacion else None
            ultimo_parcial = max(prom) if prom else None
            ref = pron if pron is not None else prom.get(ultimo_parcial)
            if ref is not None and ref < MINIMO:
                reprob.append(f"{a.nombre} ({ref})")
        texto = ", ".join(f"P{p}={v}" for p, v in por_parcial.items())
        riesgo = f" * RIESGO en: {'; '.join(reprob)}" if reprob else ""
        lineas.append(f"- {al.nombre_completo} ({al.numero_cuenta}): {texto}{riesgo}")
    return "\n".join(lineas)


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
