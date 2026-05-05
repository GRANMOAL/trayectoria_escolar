import json
import io
import base64
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.db.models import Avg, Count, Q
from django.contrib import messages
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .models import Semestre, Grupo, Alumno, Asignatura, Calificacion


def get_plot_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=120, transparent=True)
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return img_base64


def dashboard(request):
    semestres = Semestre.objects.all()
    grupos = Grupo.objects.all()
    total_alumnos = Alumno.objects.count()
    total_calificaciones = Calificacion.objects.count()

    # Estadísticas globales
    califs = list(Calificacion.objects.all())
    aprobados = sum(1 for c in califs if c.aprobado)
    reprobados = len(califs) - aprobados
    pct_aprobacion = round(aprobados / len(califs) * 100, 1) if califs else 0
    pct_reprobacion = round(reprobados / len(califs) * 100, 1) if califs else 0

    context = {
        'semestres': semestres,
        'grupos': grupos,
        'total_alumnos': total_alumnos,
        'total_calificaciones': total_calificaciones,
        'aprobados': aprobados,
        'reprobados': reprobados,
        'pct_aprobacion': pct_aprobacion,
        'pct_reprobacion': pct_reprobacion,
    }
    return render(request, 'escolar/dashboard.html', context)


def lista_alumnos(request):
    grupo_id = request.GET.get('grupo')
    grupos = Grupo.objects.select_related('semestre').all()
    alumnos = Alumno.objects.select_related('grupo__semestre').all()
    grupo_sel = None

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = alumnos.filter(grupo_id=grupo_id)

    context = {'alumnos': alumnos, 'grupos': grupos, 'grupo_sel': grupo_sel}
    return render(request, 'escolar/lista_alumnos.html', context)


def calificaciones_grupo(request):
    grupo_id = request.GET.get('grupo')
    parcial = request.GET.get('parcial', '1')
    grupos = Grupo.objects.select_related('semestre').all()
    datos = []
    grupo_sel = None
    graficas = {}

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = Alumno.objects.filter(grupo=grupo_sel)
        asignaturas = Asignatura.objects.filter(semestre=grupo_sel.semestre)

        for alumno in alumnos:
            row = {'alumno': alumno, 'calificaciones': {}}
            for asig in asignaturas:
                try:
                    cal = Calificacion.objects.get(alumno=alumno, asignatura=asig, parcial=int(parcial))
                    row['calificaciones'][asig.clave] = cal
                except Calificacion.DoesNotExist:
                    row['calificaciones'][asig.clave] = None
            datos.append(row)

        # Gráfica de promedios por asignatura
        promedios_asig = []
        nombres_asig = []
        for asig in asignaturas:
            califs_asig = [c for r in datos for clave, c in r['calificaciones'].items() if clave == asig.clave and c]
            if califs_asig:
                prom = sum(c.promedio for c in califs_asig) / len(califs_asig)
                promedios_asig.append(prom)
                nombres_asig.append(asig.nombre[:15])

        if promedios_asig:
            fig, ax = plt.subplots(figsize=(9, 4))
            fig.patch.set_alpha(0)
            ax.set_facecolor('#0d1117')
            colors = ['#00e5ff' if p >= 6 else '#ff4444' for p in promedios_asig]
            bars = ax.bar(nombres_asig, promedios_asig, color=colors, edgecolor='#ffffff22', linewidth=0.5)
            ax.axhline(y=6, color='#ffff00', linestyle='--', linewidth=1.5, label='Mínimo aprobatorio (6.0)')
            ax.set_ylim(0, 10)
            ax.set_xlabel('Asignatura', color='white', fontsize=10)
            ax.set_ylabel('Promedio', color='white', fontsize=10)
            ax.set_title(f'Promedios por Asignatura — Parcial {parcial}', color='white', fontsize=12, fontweight='bold')
            ax.tick_params(colors='white', labelsize=8)
            ax.spines[:].set_color('#ffffff33')
            for bar, val in zip(bars, promedios_asig):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1, f'{val:.1f}',
                        ha='center', va='bottom', color='white', fontsize=8, fontweight='bold')
            legend = ax.legend(facecolor='#1a1a2e', edgecolor='#ffffff33', labelcolor='white', fontsize=8)
            plt.xticks(rotation=30, ha='right')
            plt.tight_layout()
            graficas['asignaturas'] = get_plot_base64(fig)

        context = {
            'grupos': grupos, 'grupo_sel': grupo_sel, 'asignaturas': asignaturas,
            'datos': datos, 'parcial': parcial, 'graficas': graficas,
            'parciales': [1, 2, 3],
        }
    else:
        context = {'grupos': grupos, 'parcial': parcial, 'parciales': [1, 2, 3]}

    return render(request, 'escolar/calificaciones.html', context)


def indicadores(request):
    grupo_id = request.GET.get('grupo')
    grupos = Grupo.objects.select_related('semestre').all()
    graficas = {}
    stats = {}
    grupo_sel = None

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = list(Alumno.objects.filter(grupo=grupo_sel))
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))
        califs_all = list(Calificacion.objects.filter(alumno__grupo=grupo_sel))

        # Promedio general del grupo
        promedios_alumno = {}
        for alumno in alumnos:
            califs_al = [c for c in califs_all if c.alumno_id == alumno.id]
            if califs_al:
                promedios_alumno[alumno.nombre_completo] = round(sum(c.promedio for c in califs_al) / len(califs_al), 2)

        aprobados_alumnos = [a for a, p in promedios_alumno.items() if p >= 6]
        reprobados_alumnos = [a for a, p in promedios_alumno.items() if p < 6]

        stats['total'] = len(alumnos)
        stats['aprobados'] = len(aprobados_alumnos)
        stats['reprobados'] = len(reprobados_alumnos)
        stats['pct_aprobacion'] = round(stats['aprobados'] / stats['total'] * 100, 1) if stats['total'] else 0
        stats['pct_reprobacion'] = round(stats['reprobados'] / stats['total'] * 100, 1) if stats['total'] else 0
        stats['promedio_grupo'] = round(sum(promedios_alumno.values()) / len(promedios_alumno), 2) if promedios_alumno else 0

        # Gráfica 1: Pastel aprobados/reprobados
        fig1, ax1 = plt.subplots(figsize=(5, 4))
        fig1.patch.set_alpha(0)
        ax1.set_facecolor('#0d1117')
        sizes = [stats['aprobados'], stats['reprobados']]
        labels = [f"Aprobados\n{stats['aprobados']}", f"Reprobados\n{stats['reprobados']}"]
        colors = ['#00e5ff', '#ff4444']
        wedges, texts, autotexts = ax1.pie(
            sizes, labels=labels, colors=colors, autopct='%1.1f%%',
            startangle=90, wedgeprops={'edgecolor': '#0d1117', 'linewidth': 2}
        )
        for text in texts + autotexts:
            text.set_color('white')
            text.set_fontsize(10)
        ax1.set_title('Índice de Aprobación / Reprobación', color='white', fontsize=11, fontweight='bold')
        graficas['pastel'] = get_plot_base64(fig1)

        # Gráfica 2: Promedios por alumno
        if promedios_alumno:
            nombres = [n[:20] for n in promedios_alumno.keys()]
            promedios = list(promedios_alumno.values())
            fig2, ax2 = plt.subplots(figsize=(10, 5))
            fig2.patch.set_alpha(0)
            ax2.set_facecolor('#0d1117')
            colors2 = ['#00e5ff' if p >= 6 else '#ff4444' for p in promedios]
            bars = ax2.barh(nombres, promedios, color=colors2, edgecolor='#ffffff22', height=0.6)
            ax2.axvline(x=6, color='#ffff00', linestyle='--', linewidth=1.5)
            ax2.set_xlim(0, 10)
            ax2.set_xlabel('Promedio', color='white')
            ax2.set_title('Promedio por Alumno', color='white', fontsize=12, fontweight='bold')
            ax2.tick_params(colors='white', labelsize=8)
            ax2.spines[:].set_color('#ffffff33')
            for bar, val in zip(bars, promedios):
                ax2.text(val + 0.1, bar.get_y() + bar.get_height()/2, f'{val:.1f}',
                         va='center', color='white', fontsize=8, fontweight='bold')
            plt.tight_layout()
            graficas['alumnos'] = get_plot_base64(fig2)

        # Gráfica 3: Promedio por parcial
        promedios_parcial = {}
        for p in [1, 2, 3]:
            califs_p = [c for c in califs_all if c.parcial == p]
            if califs_p:
                promedios_parcial[f'P{p}'] = round(sum(c.promedio for c in califs_p) / len(califs_p), 2)

        if promedios_parcial:
            fig3, ax3 = plt.subplots(figsize=(5, 4))
            fig3.patch.set_alpha(0)
            ax3.set_facecolor('#0d1117')
            ax3.plot(list(promedios_parcial.keys()), list(promedios_parcial.values()),
                     color='#00e5ff', marker='o', markersize=10, linewidth=2.5, markerfacecolor='white')
            ax3.fill_between(list(promedios_parcial.keys()), list(promedios_parcial.values()),
                             alpha=0.2, color='#00e5ff')
            ax3.axhline(y=6, color='#ffff00', linestyle='--', linewidth=1.5)
            ax3.set_ylim(0, 10)
            ax3.set_title('Evolución por Parcial', color='white', fontsize=11, fontweight='bold')
            ax3.tick_params(colors='white')
            ax3.spines[:].set_color('#ffffff33')
            for x, y in zip(promedios_parcial.keys(), promedios_parcial.values()):
                ax3.text(x, y + 0.3, f'{y:.1f}', ha='center', color='white', fontsize=9, fontweight='bold')
            plt.tight_layout()
            graficas['parciales'] = get_plot_base64(fig3)

        # Asignaturas reprobadas con IA pronóstico
        asig_reprobadas = []
        for asig in asignaturas:
            califs_asig = [c for c in califs_all if c.asignatura_id == asig.id]
            rep = [c for c in califs_asig if not c.aprobado]
            if rep:
                alumnos_rep = list(set(c.alumno.nombre_completo for c in rep))
                prom_asig = sum(c.promedio for c in califs_asig) / len(califs_asig) if califs_asig else 0
                # Pronóstico IA simple basado en tendencia
                promedios_por_parcial = []
                for p in [1, 2, 3]:
                    califs_p = [c for c in califs_asig if c.parcial == p]
                    if califs_p:
                        promedios_por_parcial.append(sum(c.promedio for c in califs_p) / len(califs_p))

                pronostico = 'Estable'
                if len(promedios_por_parcial) >= 2:
                    tendencia = promedios_por_parcial[-1] - promedios_por_parcial[0]
                    if tendencia > 0.5:
                        pronostico = '📈 Mejora'
                    elif tendencia < -0.5:
                        pronostico = '📉 Riesgo alto'
                    else:
                        pronostico = '➡️ Estable'

                asig_reprobadas.append({
                    'asignatura': asig,
                    'alumnos': alumnos_rep,
                    'total_reprobados': len(alumnos_rep),
                    'promedio': round(prom_asig, 2),
                    'pronostico': pronostico,
                })

        stats['asig_reprobadas'] = asig_reprobadas

    context = {'grupos': grupos, 'grupo_sel': grupo_sel, 'stats': stats, 'graficas': graficas}
    return render(request, 'escolar/indicadores.html', context)


def exportar_excel(request):
    grupo_id = request.GET.get('grupo')
    parcial = request.GET.get('parcial', 'todos')

    if not grupo_id:
        messages.error(request, 'Selecciona un grupo')
        return redirect('lista_alumnos')

    grupo = get_object_or_404(Grupo, id=grupo_id)
    alumnos = Alumno.objects.filter(grupo=grupo).order_by('nombre_completo')
    asignaturas = Asignatura.objects.filter(semestre=grupo.semestre)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Estilos
    header_font = Font(bold=True, color='FFFFFF', size=11)
    header_fill = PatternFill(start_color='1a2942', end_color='1a2942', fill_type='solid')
    accent_fill = PatternFill(start_color='00b4d8', end_color='00b4d8', fill_type='solid')
    aprobado_fill = PatternFill(start_color='d4edda', end_color='d4edda', fill_type='solid')
    reprobado_fill = PatternFill(start_color='f8d7da', end_color='f8d7da', fill_type='solid')
    center = Alignment(horizontal='center', vertical='center')
    border = Border(
        left=Side(style='thin', color='cccccc'),
        right=Side(style='thin', color='cccccc'),
        top=Side(style='thin', color='cccccc'),
        bottom=Side(style='thin', color='cccccc'),
    )

    parciales_a_procesar = [1, 2, 3] if parcial == 'todos' else [int(parcial)]

    for p in parciales_a_procesar:
        ws = wb.create_sheet(title=f'Parcial {p}')
        ws.sheet_view.showGridLines = True

        # Título
        ws.merge_cells('A1:H1')
        ws['A1'] = f'TRAYECTORIA ESCOLAR — {grupo.semestre.nombre} — Grupo {grupo.nombre} — Parcial {p}'
        ws['A1'].font = Font(bold=True, color='FFFFFF', size=13)
        ws['A1'].fill = PatternFill(start_color='0d1b2a', end_color='0d1b2a', fill_type='solid')
        ws['A1'].alignment = center
        ws.row_dimensions[1].height = 28

        # Encabezados
        headers = ['N°', 'No. Cuenta', 'Nombre Completo', 'Asignatura', 'Hetero', 'Coevaluación', 'Autoevaluación', 'Promedio']
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=2, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border

        widths = [5, 15, 35, 30, 12, 14, 14, 12]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

        row_num = 3
        for idx, alumno in enumerate(alumnos, 1):
            for asig in asignaturas:
                try:
                    cal = Calificacion.objects.get(alumno=alumno, asignatura=asig, parcial=p)
                    promedio = cal.promedio
                    fill_row = aprobado_fill if cal.aprobado else reprobado_fill
                    row_data = [idx, alumno.numero_cuenta, alumno.nombre_completo, asig.nombre,
                                float(cal.heteroevaluacion), float(cal.coevaluacion),
                                float(cal.autoevaluacion), promedio]
                except Calificacion.DoesNotExist:
                    fill_row = PatternFill(start_color='fff3cd', end_color='fff3cd', fill_type='solid')
                    row_data = [idx, alumno.numero_cuenta, alumno.nombre_completo, asig.nombre, 'N/A', 'N/A', 'N/A', 'N/A']

                for col, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_num, column=col, value=val)
                    cell.fill = fill_row
                    cell.border = border
                    cell.alignment = center if col in [1, 2, 5, 6, 7, 8] else Alignment(vertical='center')
                row_num += 1

        # Hoja Lista Original
    ws_lista = wb.create_sheet(title='Lista Original')
    ws_lista.merge_cells('A1:D1')
    ws_lista['A1'] = f'LISTA DE ALUMNOS — {grupo.semestre.nombre} — Grupo {grupo.nombre}'
    ws_lista['A1'].font = Font(bold=True, color='FFFFFF', size=13)
    ws_lista['A1'].fill = PatternFill(start_color='0d1b2a', end_color='0d1b2a', fill_type='solid')
    ws_lista['A1'].alignment = center
    ws_lista.row_dimensions[1].height = 25

    for col, h in enumerate(['N°', 'No. Cuenta', 'Nombre Completo', 'Correo Electrónico'], 1):
        c = ws_lista.cell(row=2, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center
        c.border = border

    for col_w, w in zip(['A', 'B', 'C', 'D'], [5, 15, 40, 35]):
        ws_lista.column_dimensions[col_w].width = w

    alt_fill = PatternFill(start_color='e8f4fd', end_color='e8f4fd', fill_type='solid')
    for i, alumno in enumerate(alumnos, 1):
        row_fill = alt_fill if i % 2 == 0 else PatternFill(fill_type=None)
        for col, val in enumerate([i, alumno.numero_cuenta, alumno.nombre_completo, alumno.email], 1):
            c = ws_lista.cell(row=i+2, column=col, value=val)
            c.fill = row_fill
            c.border = border
            c.alignment = center if col in [1, 2] else Alignment(vertical='center')

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    nombre_archivo = f"trayectoria_{grupo.semestre.nombre.replace(' ', '_')}_G{grupo.nombre}_P{parcial}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    wb.save(response)
    return response


def pronostico_ia(request):
    grupo_id = request.GET.get('grupo')
    grupos = Grupo.objects.select_related('semestre').all()
    resultado = []
    grupo_sel = None
    graficas = {}

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = list(Alumno.objects.filter(grupo=grupo_sel))
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))

        for alumno in alumnos:
            datos_alumno = {'alumno': alumno, 'asignaturas': []}
            for asig in asignaturas:
                califs = Calificacion.objects.filter(alumno=alumno, asignatura=asig).order_by('parcial')
                promedios = [c.promedio for c in califs]

                if len(promedios) >= 2:
                    x = np.array(range(1, len(promedios)+1))
                    y = np.array(promedios)
                    coef = np.polyfit(x, y, 1)
                    pronostico_p3 = round(float(np.polyval(coef, 3)), 2)
                    pronostico_p3 = max(0, min(10, pronostico_p3))
                    tendencia = 'mejora' if coef[0] > 0.2 else ('baja' if coef[0] < -0.2 else 'estable')
                elif len(promedios) == 1:
                    pronostico_p3 = promedios[0]
                    tendencia = 'sin datos'
                else:
                    pronostico_p3 = None
                    tendencia = 'sin datos'

                datos_alumno['asignaturas'].append({
                    'asignatura': asig,
                    'promedios': promedios,
                    'pronostico': pronostico_p3,
                    'tendencia': tendencia,
                    'riesgo': pronostico_p3 is not None and pronostico_p3 < 6,
                })
            resultado.append(datos_alumno)

        # Gráfica de riesgo por asignatura
        riesgo_asig = {}
        for asig in asignaturas:
            en_riesgo = sum(
                1 for d in resultado
                for a in d['asignaturas']
                if a['asignatura'].id == asig.id and a['riesgo']
            )
            riesgo_asig[asig.nombre[:15]] = en_riesgo

        if riesgo_asig:
            fig, ax = plt.subplots(figsize=(9, 4))
            fig.patch.set_alpha(0)
            ax.set_facecolor('#0d1117')
            names = list(riesgo_asig.keys())
            vals = list(riesgo_asig.values())
            cols = ['#ff4444' if v > 0 else '#00e5ff' for v in vals]
            ax.bar(names, vals, color=cols, edgecolor='#ffffff22')
            ax.set_ylabel('Alumnos en riesgo', color='white')
            ax.set_title('Pronóstico IA — Alumnos en Riesgo por Asignatura', color='white', fontsize=12, fontweight='bold')
            ax.tick_params(colors='white', labelsize=8)
            ax.spines[:].set_color('#ffffff33')
            plt.xticks(rotation=30, ha='right')
            plt.tight_layout()
            graficas['riesgo'] = get_plot_base64(fig)

    context = {'grupos': grupos, 'grupo_sel': grupo_sel, 'resultado': resultado, 'graficas': graficas}
    return render(request, 'escolar/pronostico.html', context)
