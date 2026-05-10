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
import numpy as np
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .models import Semestre, Grupo, Alumno, Asignatura, Calificacion


# ─── Utilidades ──────────────────────────────────────────────────────────────

def get_plot_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=120, transparent=True)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return b64

def estilo_excel():
    return {
        'header_font': Font(bold=True, color='FFFFFF', size=11),
        'header_fill': PatternFill(start_color='1a2942', end_color='1a2942', fill_type='solid'),
        'title_fill': PatternFill(start_color='0d1b2a', end_color='0d1b2a', fill_type='solid'),
        'aprobado_fill': PatternFill(start_color='d4edda', end_color='d4edda', fill_type='solid'),
        'reprobado_fill': PatternFill(start_color='f8d7da', end_color='f8d7da', fill_type='solid'),
        'warn_fill': PatternFill(start_color='fff3cd', end_color='fff3cd', fill_type='solid'),
        'center': Alignment(horizontal='center', vertical='center'),
        'border': Border(
            left=Side(style='thin', color='cccccc'), right=Side(style='thin', color='cccccc'),
            top=Side(style='thin', color='cccccc'), bottom=Side(style='thin', color='cccccc'),
        ),
    }


# ─── Dashboard ───────────────────────────────────────────────────────────────

def dashboard(request):
    semestres = Semestre.objects.all()
    grupos = Grupo.objects.all()
    total_alumnos = Alumno.objects.count()
    total_calificaciones = Calificacion.objects.count()

    califs = list(Calificacion.objects.all())
    aprobados = sum(1 for c in califs if c.aprobado)
    reprobados = len(califs) - aprobados
    pct_aprobacion = round(aprobados / len(califs) * 100, 1) if califs else 0
    pct_reprobacion = round(reprobados / len(califs) * 100, 1) if califs else 0

    context = {
        'semestres': semestres, 'grupos': grupos,
        'total_alumnos': total_alumnos, 'total_calificaciones': total_calificaciones,
        'aprobados': aprobados, 'reprobados': reprobados,
        'pct_aprobacion': pct_aprobacion, 'pct_reprobacion': pct_reprobacion,
    }
    return render(request, 'escolar/dashboard.html', context)


# ─── Lista Alumnos ────────────────────────────────────────────────────────────

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


# ─── Calificaciones ───────────────────────────────────────────────────────────

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
            ax.set_facecolor('#f8fafc')
            colors = ['#4f46e5' if p >= 6 else '#ef4444' for p in promedios_asig]
            bars = ax.bar(nombres_asig, promedios_asig, color=colors, edgecolor='#cbd5e1', linewidth=0.5)
            ax.axhline(y=6, color='#ef4444', linestyle='--', linewidth=1.5, label='Mínimo aprobatorio (6.0)')
            ax.set_ylim(0, 10)
            ax.set_xlabel('Asignatura', color='#1e293b', fontsize=10)
            ax.set_ylabel('Promedio', color='#1e293b', fontsize=10)
            ax.set_title(f'Promedios por Asignatura — Parcial {parcial}', color='#1e293b', fontsize=12, fontweight='bold')
            ax.tick_params(colors='#475569', labelsize=8)
            ax.spines[:].set_color('#e2e8f0')
            for bar, val in zip(bars, promedios_asig):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1, f'{val:.1f}',
                        ha='center', va='bottom', color='#1e293b', fontsize=8, fontweight='bold')
            ax.legend(facecolor='white', edgecolor='#e2e8f0', labelcolor='#1e293b', fontsize=8)
            plt.xticks(rotation=30, ha='right')
            plt.tight_layout()
            graficas['asignaturas'] = get_plot_base64(fig)

        context = {
            'grupos': grupos, 'grupo_sel': grupo_sel, 'asignaturas': asignaturas,
            'datos': datos, 'parcial': parcial, 'graficas': graficas, 'parciales': [1, 2, 3],
        }
    else:
        context = {'grupos': grupos, 'parcial': parcial, 'parciales': [1, 2, 3]}

    return render(request, 'escolar/calificaciones.html', context)


# ─── Indicadores ─────────────────────────────────────────────────────────────

def indicadores(request):
    grupo_id = request.GET.get('grupo')
    asig_filtro = request.GET.get('asignatura', '')
    parcial_filtro = request.GET.get('parcial', '')
    grupos = Grupo.objects.select_related('semestre').all()
    graficas = {}
    stats = {}
    grupo_sel = None
    asignaturas_resumen = []

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = list(Alumno.objects.filter(grupo=grupo_sel))
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))
        califs_all = list(Calificacion.objects.filter(alumno__grupo=grupo_sel).select_related('alumno', 'asignatura'))

        # Filtros dinámicos
        califs_filtradas = califs_all
        if asig_filtro:
            califs_filtradas = [c for c in califs_filtradas if str(c.asignatura_id) == asig_filtro]
        if parcial_filtro:
            califs_filtradas = [c for c in califs_filtradas if str(c.parcial) == parcial_filtro]

        # Promedios por alumno (con filtros)
        promedios_alumno = {}
        for alumno in alumnos:
            cal_al = [c for c in califs_filtradas if c.alumno_id == alumno.id]
            if cal_al:
                promedios_alumno[alumno.nombre_completo] = round(sum(c.promedio for c in cal_al) / len(cal_al), 2)

        stats['total'] = len(alumnos)
        stats['aprobados'] = sum(1 for p in promedios_alumno.values() if p >= 6)
        stats['reprobados'] = sum(1 for p in promedios_alumno.values() if p < 6)
        stats['pct_aprobacion'] = round(stats['aprobados'] / stats['total'] * 100, 1) if stats['total'] else 0
        stats['pct_reprobacion'] = round(stats['reprobados'] / stats['total'] * 100, 1) if stats['total'] else 0
        stats['promedio_grupo'] = round(sum(promedios_alumno.values()) / len(promedios_alumno), 2) if promedios_alumno else 0

        # Resumen por asignatura (dinámico con filtros)
        for asig in asignaturas:
            if asig_filtro and str(asig.id) != asig_filtro:
                continue
            cals = [c for c in califs_all if c.asignatura_id == asig.id]
            if parcial_filtro:
                cals = [c for c in cals if str(c.parcial) == parcial_filtro]
            if not cals:
                continue
            ap = sum(1 for c in cals if c.aprobado)
            rep = len(cals) - ap
            prom = round(sum(c.promedio for c in cals) / len(cals), 2)
            # Promedio por parcial
            por_parcial = {}
            for p in [1, 2, 3]:
                cp = [c for c in cals if c.parcial == p]
                if cp:
                    por_parcial[p] = round(sum(c.promedio for c in cp) / len(cp), 2)
            asignaturas_resumen.append({
                'asignatura': asig,
                'total': len(set(c.alumno_id for c in cals)),
                'aprobados': ap,
                'reprobados': rep,
                'promedio': prom,
                'pct_aprobacion': round(ap / len(cals) * 100, 1) if cals else 0,
                'por_parcial': por_parcial,
            })

        # Gráfica pastel
        if stats['aprobados'] + stats['reprobados'] > 0:
            fig1, ax1 = plt.subplots(figsize=(5, 4))
            fig1.patch.set_alpha(0); ax1.set_facecolor('#f8fafc')
            sizes = [stats['aprobados'], stats['reprobados']]
            if all(s == 0 for s in sizes):
                sizes = [1, 1]
            wedges, texts, autotexts = ax1.pie(
                sizes, labels=[f"Aprobados\n{stats['aprobados']}", f"Reprobados\n{stats['reprobados']}"],
                colors=['#00e5ff', '#ff4444'], autopct='%1.1f%%',
                startangle=90, wedgeprops={'edgecolor': 'white', 'linewidth': 2.5}
            )
            for t in texts + autotexts:
                t.set_color('#1e293b'); t.set_fontsize(10)
            ax1.set_title('Aprobación / Reprobación', color='#1e293b', fontsize=11, fontweight='bold')
            graficas['pastel'] = get_plot_base64(fig1)

        # Gráfica promedios por alumno
        if promedios_alumno:
            nombres = [n[:22] for n in promedios_alumno.keys()]
            promedios = list(promedios_alumno.values())
            fig2, ax2 = plt.subplots(figsize=(10, max(4, len(nombres) * 0.45)))
            fig2.patch.set_alpha(0); ax2.set_facecolor('#f8fafc')
            colors2 = ['#4f46e5' if p >= 6 else '#ef4444' for p in promedios]
            bars = ax2.barh(nombres, promedios, color=colors2, edgecolor='#cbd5e1', height=0.6)
            ax2.axvline(x=6, color='#ef4444', linestyle='--', linewidth=1.5)
            ax2.set_xlim(0, 10); ax2.set_xlabel('Promedio', color='#64748b')
            ax2.set_title('Promedio por Alumno', color='#1e293b', fontsize=12, fontweight='bold')
            ax2.tick_params(colors='#475569', labelsize=8); ax2.spines[:].set_color('#e2e8f0')
            for bar, val in zip(bars, promedios):
                ax2.text(val + 0.1, bar.get_y() + bar.get_height()/2, f'{val:.1f}',
                         va='center', color='#1e293b', fontsize=8, fontweight='bold')
            plt.tight_layout()
            graficas['alumnos'] = get_plot_base64(fig2)

        # Gráfica evolución parciales
        promedios_parcial = {}
        for p in [1, 2, 3]:
            cp = [c for c in califs_filtradas if c.parcial == p]
            if cp:
                promedios_parcial[f'P{p}'] = round(sum(c.promedio for c in cp) / len(cp), 2)

        if len(promedios_parcial) >= 2:
            fig3, ax3 = plt.subplots(figsize=(5, 4))
            fig3.patch.set_alpha(0); ax3.set_facecolor('#f8fafc')
            ax3.plot(list(promedios_parcial.keys()), list(promedios_parcial.values()),
                     color='#4f46e5', marker='o', markersize=10, linewidth=2.5, markerfacecolor='white')
            ax3.fill_between(list(promedios_parcial.keys()), list(promedios_parcial.values()), alpha=0.15, color='#4f46e5')
            ax3.axhline(y=6, color='#ef4444', linestyle='--', linewidth=1.5)
            ax3.set_ylim(0, 10); ax3.set_title('Evolución por Parcial', color='#1e293b', fontsize=11, fontweight='bold')
            ax3.tick_params(colors='#475569'); ax3.spines[:].set_color('#e2e8f0')
            for x, y in zip(promedios_parcial.keys(), promedios_parcial.values()):
                ax3.text(x, y + 0.3, f'{y:.1f}', ha='center', color='#1e293b', fontsize=9, fontweight='bold')
            plt.tight_layout()
            graficas['parciales'] = get_plot_base64(fig3)

        # Gráfica comparativa asignaturas
        if asignaturas_resumen:
            nombres_asig = [a['asignatura'].nombre[:14] for a in asignaturas_resumen]
            proms_asig = [a['promedio'] for a in asignaturas_resumen]
            fig4, ax4 = plt.subplots(figsize=(9, 4))
            fig4.patch.set_alpha(0); ax4.set_facecolor('#f8fafc')
            colors4 = ['#4f46e5' if p >= 6 else '#ef4444' for p in proms_asig]
            bars4 = ax4.bar(nombres_asig, proms_asig, color=colors4, edgecolor='#cbd5e1')
            ax4.axhline(y=6, color='#ef4444', linestyle='--', linewidth=1.5)
            ax4.set_ylim(0, 10); ax4.set_ylabel('Promedio', color='#64748b')
            ax4.set_title('Promedio por Asignatura', color='#1e293b', fontsize=12, fontweight='bold')
            ax4.tick_params(colors='#475569', labelsize=8); ax4.spines[:].set_color('#e2e8f0')
            for bar, val in zip(bars4, proms_asig):
                ax4.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1, f'{val:.1f}',
                         ha='center', va='bottom', color='#1e293b', fontsize=8, fontweight='bold')
            plt.xticks(rotation=30, ha='right'); plt.tight_layout()
            graficas['asignaturas'] = get_plot_base64(fig4)

        # Asignaturas reprobadas
        asig_reprobadas = []
        for asig in asignaturas:
            cals = [c for c in califs_all if c.asignatura_id == asig.id]
            rep = [c for c in cals if not c.aprobado]
            if rep:
                promedios_por_parcial = []
                for p in [1, 2, 3]:
                    cp = [c for c in cals if c.parcial == p]
                    if cp:
                        promedios_por_parcial.append(sum(c.promedio for c in cp) / len(cp))
                pronostico = '➡️ Estable'
                if len(promedios_por_parcial) >= 2:
                    tendencia = promedios_por_parcial[-1] - promedios_por_parcial[0]
                    pronostico = '📈 Mejora' if tendencia > 0.5 else ('📉 Riesgo alto' if tendencia < -0.5 else '➡️ Estable')
                prom_asig = sum(c.promedio for c in cals) / len(cals) if cals else 0
                asig_reprobadas.append({
                    'asignatura': asig,
                    'alumnos': list(set(c.alumno.nombre_completo for c in rep)),
                    'total_reprobados': len(set(c.alumno_id for c in rep)),
                    'promedio': round(prom_asig, 2),
                    'pronostico': pronostico,
                })
        stats['asig_reprobadas'] = asig_reprobadas

    context = {
        'grupos': grupos, 'grupo_sel': grupo_sel, 'stats': stats, 'graficas': graficas,
        'asignaturas_resumen': asignaturas_resumen,
        'asignaturas_lista': list(Asignatura.objects.filter(semestre=grupo_sel.semestre)) if grupo_sel else [],
        'asig_filtro': asig_filtro,
        'parcial_filtro': parcial_filtro,
    }
    return render(request, 'escolar/indicadores.html', context)


# ─── Exportar Excel (calificaciones) ─────────────────────────────────────────

def exportar_excel(request):
    grupo_id = request.GET.get('grupo')
    parcial = request.GET.get('parcial', 'todos')
    if not grupo_id:
        messages.error(request, 'Selecciona un grupo')
        return redirect('lista_alumnos')

    grupo = get_object_or_404(Grupo, id=grupo_id)
    alumnos = Alumno.objects.filter(grupo=grupo).order_by('nombre_completo')
    asignaturas = Asignatura.objects.filter(semestre=grupo.semestre)
    e = estilo_excel()

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    parciales_a_procesar = [1, 2, 3] if parcial == 'todos' else [int(parcial)]

    for p in parciales_a_procesar:
        ws = wb.create_sheet(title=f'Parcial {p}')
        ws.merge_cells('A1:H1')
        ws['A1'] = f'TRAYECTORIA ESCOLAR — {grupo.semestre.nombre} — Grupo {grupo.nombre} — Parcial {p}'
        ws['A1'].font = Font(bold=True, color='FFFFFF', size=13)
        ws['A1'].fill = e['title_fill']
        ws['A1'].alignment = e['center']
        ws.row_dimensions[1].height = 28
        for col, h in enumerate(['N°', 'No. Cuenta', 'Nombre Completo', 'Asignatura', 'Hetero', 'Coevaluación', 'Autoevaluación', 'Promedio'], 1):
            cell = ws.cell(row=2, column=col, value=h)
            cell.font = e['header_font']; cell.fill = e['header_fill']
            cell.alignment = e['center']; cell.border = e['border']
        for i, w in enumerate([5, 15, 35, 30, 12, 14, 14, 12], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        row_num = 3
        for idx, alumno in enumerate(alumnos, 1):
            for asig in asignaturas:
                try:
                    cal = Calificacion.objects.get(alumno=alumno, asignatura=asig, parcial=p)
                    fill_row = e['aprobado_fill'] if cal.aprobado else e['reprobado_fill']
                    row_data = [idx, alumno.numero_cuenta, alumno.nombre_completo, asig.nombre,
                                float(cal.heteroevaluacion), float(cal.coevaluacion), float(cal.autoevaluacion), cal.promedio]
                except Calificacion.DoesNotExist:
                    fill_row = e['warn_fill']
                    row_data = [idx, alumno.numero_cuenta, alumno.nombre_completo, asig.nombre, 'N/A', 'N/A', 'N/A', 'N/A']
                for col, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_num, column=col, value=val)
                    cell.fill = fill_row; cell.border = e['border']
                    cell.alignment = e['center'] if col in [1, 2, 5, 6, 7, 8] else Alignment(vertical='center')
                row_num += 1

    ws_lista = wb.create_sheet(title='Lista Original')
    ws_lista.merge_cells('A1:D1')
    ws_lista['A1'] = f'LISTA DE ALUMNOS — {grupo.semestre.nombre} — Grupo {grupo.nombre}'
    ws_lista['A1'].font = Font(bold=True, color='FFFFFF', size=13)
    ws_lista['A1'].fill = e['title_fill']; ws_lista['A1'].alignment = e['center']
    ws_lista.row_dimensions[1].height = 25
    for col, h in enumerate(['N°', 'No. Cuenta', 'Nombre Completo', 'Correo Electrónico'], 1):
        c = ws_lista.cell(row=2, column=col, value=h)
        c.font = e['header_font']; c.fill = e['header_fill']
        c.alignment = e['center']; c.border = e['border']
    for col_w, w in zip(['A', 'B', 'C', 'D'], [5, 15, 40, 35]):
        ws_lista.column_dimensions[col_w].width = w
    alt_fill = PatternFill(start_color='e8f4fd', end_color='e8f4fd', fill_type='solid')
    for i, alumno in enumerate(alumnos, 1):
        row_fill = alt_fill if i % 2 == 0 else PatternFill(fill_type=None)
        for col, val in enumerate([i, alumno.numero_cuenta, alumno.nombre_completo, alumno.email], 1):
            c = ws_lista.cell(row=i+2, column=col, value=val)
            c.fill = row_fill; c.border = e['border']
            c.alignment = e['center'] if col in [1, 2] else Alignment(vertical='center')

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="trayectoria_{grupo.semestre.nombre.replace(" ","_")}_G{grupo.nombre}_P{parcial}.xlsx"'
    wb.save(response)
    return response


# ─── Exportar Pronóstico IA en Excel ─────────────────────────────────────────

def exportar_pronostico_excel(request):
    grupo_id = request.GET.get('grupo')
    if not grupo_id:
        return redirect('pronostico')
    grupo = get_object_or_404(Grupo, id=grupo_id)
    alumnos = list(Alumno.objects.filter(grupo=grupo).order_by('nombre_completo'))
    asignaturas = list(Asignatura.objects.filter(semestre=grupo.semestre))
    e = estilo_excel()

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Hoja resumen grupo
    ws = wb.create_sheet('Pronóstico General')
    ws.merge_cells('A1:G1')
    ws['A1'] = f'PRONÓSTICO IA — {grupo.semestre.nombre} — Grupo {grupo.nombre}'
    ws['A1'].font = Font(bold=True, color='FFFFFF', size=13)
    ws['A1'].fill = e['title_fill']; ws['A1'].alignment = e['center']
    ws.row_dimensions[1].height = 28
    for col, h in enumerate(['Alumno', 'No. Cuenta', 'Promedio P1', 'Promedio P2', 'Promedio P3', 'Pronóstico P3 (IA)', 'Tendencia'], 1):
        c = ws.cell(row=2, column=col, value=h)
        c.font = e['header_font']; c.fill = e['header_fill']
        c.alignment = e['center']; c.border = e['border']
    for i, w in enumerate([35, 15, 14, 14, 14, 18, 15], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    califs_all = list(Calificacion.objects.filter(alumno__grupo=grupo).select_related('alumno', 'asignatura'))
    row_num = 3
    aprobados_grupo = 0
    reprobados_grupo = 0
    for alumno in alumnos:
        cal_al = [c for c in califs_all if c.alumno_id == alumno.id]
        promedios_parcial = {}
        for p in [1, 2, 3]:
            cp = [c for c in cal_al if c.parcial == p]
            if cp:
                promedios_parcial[p] = round(sum(c.promedio for c in cp) / len(cp), 2)
        vals = list(promedios_parcial.values())
        if len(vals) >= 2:
            x = np.array(range(1, len(vals)+1))
            coef = np.polyfit(x, np.array(vals), 1)
            pron = round(float(np.polyval(coef, 3)), 2)
            pron = max(0, min(10, pron))
            tendencia = 'Mejora' if coef[0] > 0.2 else ('Baja' if coef[0] < -0.2 else 'Estable')
        elif len(vals) == 1:
            pron = vals[0]; tendencia = 'Sin datos suficientes'
        else:
            pron = None; tendencia = 'Sin datos'
        if pron is not None and pron >= 6:
            aprobados_grupo += 1
        elif pron is not None:
            reprobados_grupo += 1
        fill = e['aprobado_fill'] if (pron and pron >= 6) else e['reprobado_fill']
        row_vals = [
            alumno.nombre_completo, alumno.numero_cuenta,
            promedios_parcial.get(1, 'N/A'), promedios_parcial.get(2, 'N/A'), promedios_parcial.get(3, 'N/A'),
            pron if pron is not None else 'N/D', tendencia
        ]
        for col, val in enumerate(row_vals, 1):
            c = ws.cell(row=row_num, column=col, value=val)
            c.fill = fill; c.border = e['border']
            c.alignment = e['center'] if col != 1 else Alignment(vertical='center')
        row_num += 1

    # Fila de índices
    row_num += 1
    ws.cell(row=row_num, column=1, value='ÍNDICE DE APROBACIÓN').font = Font(bold=True, color='00aa55', size=11)
    ws.cell(row=row_num, column=2, value=f'{aprobados_grupo} / {len(alumnos)}').alignment = e['center']
    row_num += 1
    ws.cell(row=row_num, column=1, value='ÍNDICE DE REPROBACIÓN').font = Font(bold=True, color='cc2222', size=11)
    ws.cell(row=row_num, column=2, value=f'{reprobados_grupo} / {len(alumnos)}').alignment = e['center']

    # Hoja por asignatura
    for asig in asignaturas:
        ws2 = wb.create_sheet(title=asig.clave[:30])
        ws2.merge_cells('A1:G1')
        ws2['A1'] = f'PRONÓSTICO IA — {asig.nombre} — {grupo.semestre.nombre}'
        ws2['A1'].font = Font(bold=True, color='FFFFFF', size=12)
        ws2['A1'].fill = e['title_fill']; ws2['A1'].alignment = e['center']
        ws2.row_dimensions[1].height = 25
        for col, h in enumerate(['Alumno', 'No. Cuenta', 'P1', 'P2', 'P3', 'Pronóstico P3', 'Tendencia'], 1):
            c = ws2.cell(row=2, column=col, value=h)
            c.font = e['header_font']; c.fill = e['header_fill']
            c.alignment = e['center']; c.border = e['border']
        for i, w in enumerate([35, 15, 10, 10, 10, 16, 15], 1):
            ws2.column_dimensions[get_column_letter(i)].width = w
        row_num2 = 3
        ap2 = 0; rep2 = 0
        for alumno in alumnos:
            califs = [c for c in califs_all if c.alumno_id == alumno.id and c.asignatura_id == asig.id]
            promedios = {c.parcial: c.promedio for c in califs}
            vals = [promedios.get(p) for p in [1, 2, 3] if promedios.get(p) is not None]
            if len(vals) >= 2:
                x = np.array(range(1, len(vals)+1))
                coef = np.polyfit(x, np.array(vals), 1)
                pron = round(max(0, min(10, float(np.polyval(coef, 3)))), 2)
                tend = 'Mejora' if coef[0] > 0.2 else ('Baja' if coef[0] < -0.2 else 'Estable')
            elif len(vals) == 1:
                pron = vals[0]; tend = 'Sin datos'
            else:
                pron = None; tend = 'Sin datos'
            if pron is not None and pron >= 6: ap2 += 1
            elif pron is not None: rep2 += 1
            fill2 = e['aprobado_fill'] if (pron and pron >= 6) else e['reprobado_fill']
            row_vals2 = [alumno.nombre_completo, alumno.numero_cuenta,
                         promedios.get(1, 'N/A'), promedios.get(2, 'N/A'), promedios.get(3, 'N/A'),
                         pron if pron is not None else 'N/D', tend]
            for col, val in enumerate(row_vals2, 1):
                c = ws2.cell(row=row_num2, column=col, value=val)
                c.fill = fill2; c.border = e['border']; c.alignment = e['center']
            row_num2 += 1
        row_num2 += 1
        ws2.cell(row=row_num2, column=1, value=f'Aprobados: {ap2}  |  Reprobados: {rep2}  |  % Aprobación: {round(ap2/len(alumnos)*100,1) if alumnos else 0}%').font = Font(bold=True, size=10)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="pronostico_IA_{grupo.semestre.nombre.replace(" ","_")}_G{grupo.nombre}.xlsx"'
    wb.save(response)
    return response


# ─── Pronóstico IA ────────────────────────────────────────────────────────────

def pronostico_ia(request):
    grupo_id = request.GET.get('grupo')
    grupos = Grupo.objects.select_related('semestre').all()
    resultado = []
    grupo_sel = None
    graficas = {}
    indices_asig = []
    stats_grupo = {}

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = list(Alumno.objects.filter(grupo=grupo_sel).order_by('nombre_completo'))
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))
        califs_all = list(Calificacion.objects.filter(alumno__grupo=grupo_sel).select_related('alumno', 'asignatura'))

        for alumno in alumnos:
            datos_alumno = {'alumno': alumno, 'asignaturas': [], 'promedio_global': None}
            promedios_globales = []
            for asig in asignaturas:
                califs = [c for c in califs_all if c.alumno_id == alumno.id and c.asignatura_id == asig.id]
                promedios = [c.promedio for c in sorted(califs, key=lambda x: x.parcial)]
                if len(promedios) >= 2:
                    x = np.array(range(1, len(promedios)+1))
                    coef = np.polyfit(x, np.array(promedios), 1)
                    pron = round(max(0, min(10, float(np.polyval(coef, 3)))), 2)
                    tendencia = 'mejora' if coef[0] > 0.2 else ('baja' if coef[0] < -0.2 else 'estable')
                elif len(promedios) == 1:
                    pron = promedios[0]; tendencia = 'sin datos'
                else:
                    pron = None; tendencia = 'sin datos'
                if pron is not None:
                    promedios_globales.append(pron)
                datos_alumno['asignaturas'].append({
                    'asignatura': asig, 'promedios': promedios,
                    'pronostico': pron, 'tendencia': tendencia,
                    'riesgo': pron is not None and pron < 6,
                })
            if promedios_globales:
                datos_alumno['promedio_global'] = round(sum(promedios_globales) / len(promedios_globales), 2)
            resultado.append(datos_alumno)

        # Stats grupo
        promedios_grupo = [d['promedio_global'] for d in resultado if d['promedio_global'] is not None]
        stats_grupo['aprobados'] = sum(1 for p in promedios_grupo if p >= 6)
        stats_grupo['reprobados'] = sum(1 for p in promedios_grupo if p < 6)
        stats_grupo['total'] = len(alumnos)
        stats_grupo['pct_aprobacion'] = round(stats_grupo['aprobados'] / stats_grupo['total'] * 100, 1) if stats_grupo['total'] else 0
        stats_grupo['pct_reprobacion'] = round(stats_grupo['reprobados'] / stats_grupo['total'] * 100, 1) if stats_grupo['total'] else 0

        # Índices por asignatura
        for asig in asignaturas:
            prons_asig = [
                d['asignaturas'][i]['pronostico']
                for d in resultado
                for i, a in enumerate(d['asignaturas']) if a['asignatura'].id == asig.id and a['pronostico'] is not None
            ]
            ap = sum(1 for p in prons_asig if p >= 6)
            rep = sum(1 for p in prons_asig if p < 6)
            indices_asig.append({
                'asignatura': asig,
                'aprobados': ap, 'reprobados': rep, 'total': len(prons_asig),
                'pct_aprobacion': round(ap / len(prons_asig) * 100, 1) if prons_asig else 0,
                'promedio': round(sum(prons_asig) / len(prons_asig), 2) if prons_asig else 0,
            })

        # Gráfica riesgo
        riesgo_asig = {asig.nombre[:15]: sum(1 for d in resultado for a in d['asignaturas'] if a['asignatura'].id == asig.id and a['riesgo']) for asig in asignaturas}
        if riesgo_asig:
            fig, ax = plt.subplots(figsize=(9, 4))
            fig.patch.set_alpha(0); ax.set_facecolor('#f8fafc')
            names = list(riesgo_asig.keys()); vals = list(riesgo_asig.values())
            ax.bar(names, vals, color=['#ef4444' if v > 0 else '#4f46e5' for v in vals], edgecolor='#cbd5e1')
            ax.set_ylabel('Alumnos en riesgo', color='#64748b')
            ax.set_title('Pronóstico IA — Alumnos en Riesgo por Asignatura', color='#1e293b', fontsize=12, fontweight='bold')
            ax.tick_params(colors='#475569', labelsize=8); ax.spines[:].set_color('#e2e8f0')
            plt.xticks(rotation=30, ha='right'); plt.tight_layout()
            graficas['riesgo'] = get_plot_base64(fig)

        # Gráfica índice grupo pastel
        if stats_grupo['total'] > 0:
            fig2, ax2 = plt.subplots(figsize=(4.5, 4))
            fig2.patch.set_alpha(0); ax2.set_facecolor('#f8fafc')
            sizes = [stats_grupo['aprobados'], stats_grupo['reprobados']]
            if any(s > 0 for s in sizes):
                wedges, texts, autotexts = ax2.pie(
                    sizes, labels=[f"Aprobados\n{stats_grupo['aprobados']}", f"Reprobados\n{stats_grupo['reprobados']}"],
                    colors=['#00e5ff', '#ff4444'], autopct='%1.1f%%', startangle=90,
                    wedgeprops={'edgecolor': 'white', 'linewidth': 2.5}
                )
                for t in texts + autotexts:
                    t.set_color('#1e293b'); t.set_fontsize(10)
            ax2.set_title('Índice del Grupo (Pronóstico)', color='#1e293b', fontsize=11, fontweight='bold')
            graficas['pastel_grupo'] = get_plot_base64(fig2)

    context = {
        'grupos': grupos, 'grupo_sel': grupo_sel,
        'resultado': resultado, 'graficas': graficas,
        'indices_asig': indices_asig, 'stats_grupo': stats_grupo,
    }
    return render(request, 'escolar/pronostico.html', context)


# ─── Módulo Profesor ─────────────────────────────────────────────────────────

def profesor_panel(request):
    semestres = Semestre.objects.all()
    grupos = Grupo.objects.select_related('semestre').all()
    asignaturas = Asignatura.objects.select_related('semestre').all()
    context = {'semestres': semestres, 'grupos': grupos, 'asignaturas': asignaturas}
    return render(request, 'escolar/profesor.html', context)


def crear_estructura(request):
    if request.method != 'POST':
        return redirect('profesor')
    accion = request.POST.get('accion')

    if accion == 'semestre':
        nombre = request.POST.get('nombre_semestre', '').strip()
        anio = request.POST.get('anio', '')
        periodo = request.POST.get('periodo', '')
        if nombre and anio and periodo:
            sem, created = Semestre.objects.get_or_create(nombre=nombre, anio=int(anio), periodo=periodo)
            messages.success(request, f'Semestre "{sem.nombre}" {"creado" if created else "ya existe"}.')
        else:
            messages.error(request, 'Completa todos los campos del semestre.')

    elif accion == 'grupo':
        sem_id = request.POST.get('semestre_grupo')
        nombre_g = request.POST.get('nombre_grupo', '').strip()
        if sem_id and nombre_g:
            sem = get_object_or_404(Semestre, id=sem_id)
            g, created = Grupo.objects.get_or_create(nombre=nombre_g, semestre=sem)
            messages.success(request, f'Grupo "{g.nombre}" {"creado" if created else "ya existe"}.')
        else:
            messages.error(request, 'Selecciona semestre y nombre de grupo.')

    elif accion == 'asignatura':
        sem_id = request.POST.get('semestre_asig')
        nombre_a = request.POST.get('nombre_asig', '').strip()
        clave_a = request.POST.get('clave_asig', '').strip().upper()
        if sem_id and nombre_a and clave_a:
            sem = get_object_or_404(Semestre, id=sem_id)
            a, created = Asignatura.objects.get_or_create(clave=clave_a, defaults={'nombre': nombre_a, 'semestre': sem})
            messages.success(request, f'Asignatura "{a.nombre}" ({a.clave}) {"creada" if created else "ya existe"}.')
        else:
            messages.error(request, 'Completa todos los campos de la asignatura.')

    return redirect('profesor')


def importar_excel(request):
    if request.method != 'POST':
        return redirect('profesor')

    archivo = request.FILES.get('archivo_excel')
    grupo_id = request.POST.get('grupo_importar')
    asig_id = request.POST.get('asignatura_importar')
    parcial = request.POST.get('parcial_importar')

    if not all([archivo, grupo_id, asig_id, parcial]):
        messages.error(request, 'Completa todos los campos: archivo, grupo, asignatura y parcial.')
        return redirect('profesor')

    grupo = get_object_or_404(Grupo, id=grupo_id)
    asig = get_object_or_404(Asignatura, id=asig_id)

    try:
        wb = openpyxl.load_workbook(archivo)
        ws = wb.active
        creados = 0; actualizados = 0; errores = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not row or not row[0]:
                continue
            try:
                num_cuenta = str(row[0]).strip()
                nombre = str(row[1]).strip() if row[1] else ''
                hetero = float(row[2]) if row[2] is not None else None
                co = float(row[3]) if row[3] is not None else None
                auto = float(row[4]) if row[4] is not None else None

                if not num_cuenta or hetero is None or co is None or auto is None:
                    errores.append(f'Fila {row_idx}: datos incompletos')
                    continue
                if not (0 <= hetero <= 10 and 0 <= co <= 10 and 0 <= auto <= 10):
                    errores.append(f'Fila {row_idx}: calificaciones fuera de rango (0-10)')
                    continue

                # Crear o actualizar alumno
                alumno, al_created = Alumno.objects.get_or_create(
                    numero_cuenta=num_cuenta,
                    defaults={'nombre_completo': nombre, 'email': f'{num_cuenta}@ithi.edu.mx', 'grupo': grupo}
                )
                if al_created and nombre:
                    alumno.nombre_completo = nombre
                    alumno.grupo = grupo
                    alumno.save()

                # Crear o actualizar calificación
                cal, cal_created = Calificacion.objects.update_or_create(
                    alumno=alumno, asignatura=asig, parcial=int(parcial),
                    defaults={'heteroevaluacion': hetero, 'coevaluacion': co, 'autoevaluacion': auto}
                )
                if cal_created:
                    creados += 1
                else:
                    actualizados += 1

            except (ValueError, TypeError) as ex:
                errores.append(f'Fila {row_idx}: {ex}')

        msg = f'Importación completada: {creados} nuevos, {actualizados} actualizados.'
        if errores:
            msg += f' Errores en {len(errores)} filas: {"; ".join(errores[:3])}{"..." if len(errores) > 3 else ""}'
            messages.warning(request, msg)
        else:
            messages.success(request, msg)

    except Exception as ex:
        messages.error(request, f'Error al leer el archivo Excel: {ex}')

    return redirect('profesor')


def exportar_asignatura(request):
    asig_id = request.GET.get('asignatura')
    parcial = request.GET.get('parcial', 'todos')
    grupo_id = request.GET.get('grupo')

    if not asig_id:
        messages.error(request, 'Selecciona una asignatura.')
        return redirect('profesor')

    asig = get_object_or_404(Asignatura, id=asig_id)
    e = estilo_excel()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    parciales_a_procesar = [1, 2, 3] if parcial == 'todos' else [int(parcial)]
    alumnos_qs = Alumno.objects.filter(grupo__semestre=asig.semestre)
    if grupo_id:
        alumnos_qs = alumnos_qs.filter(grupo_id=grupo_id)
    alumnos = list(alumnos_qs.order_by('nombre_completo'))

    for p in parciales_a_procesar:
        ws = wb.create_sheet(title=f'Parcial {p}')
        ws.merge_cells('A1:G1')
        ws['A1'] = f'{asig.nombre} ({asig.clave}) — Parcial {p}'
        ws['A1'].font = Font(bold=True, color='FFFFFF', size=13)
        ws['A1'].fill = e['title_fill']; ws['A1'].alignment = e['center']
        ws.row_dimensions[1].height = 28
        for col, h in enumerate(['N°', 'No. Cuenta', 'Nombre Completo', 'Grupo', 'Heteroevaluación', 'Coevaluación', 'Autoevaluación', 'Promedio'], 1):
            cell = ws.cell(row=2, column=col, value=h)
            cell.font = e['header_font']; cell.fill = e['header_fill']
            cell.alignment = e['center']; cell.border = e['border']
        for i, w in enumerate([5, 15, 35, 10, 16, 14, 16, 12], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        row_num = 3
        for idx, alumno in enumerate(alumnos, 1):
            try:
                cal = Calificacion.objects.get(alumno=alumno, asignatura=asig, parcial=p)
                fill_row = e['aprobado_fill'] if cal.aprobado else e['reprobado_fill']
                row_data = [idx, alumno.numero_cuenta, alumno.nombre_completo, alumno.grupo.nombre,
                            float(cal.heteroevaluacion), float(cal.coevaluacion), float(cal.autoevaluacion), cal.promedio]
            except Calificacion.DoesNotExist:
                fill_row = e['warn_fill']
                row_data = [idx, alumno.numero_cuenta, alumno.nombre_completo, alumno.grupo.nombre, 'N/A', 'N/A', 'N/A', 'N/A']
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_num, column=col, value=val)
                cell.fill = fill_row; cell.border = e['border']
                cell.alignment = e['center'] if col in [1, 2, 4, 5, 6, 7, 8] else Alignment(vertical='center')
            row_num += 1

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="asignatura_{asig.clave}_P{parcial}.xlsx"'
    wb.save(response)
    return response


def descargar_plantilla(request):
    """Genera una plantilla Excel para importar calificaciones."""
    e = estilo_excel()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Calificaciones'

    ws.merge_cells('A1:E1')
    ws['A1'] = 'PLANTILLA DE IMPORTACIÓN — Sistema de Trayectoria Escolar'
    ws['A1'].font = Font(bold=True, color='FFFFFF', size=13)
    ws['A1'].fill = e['title_fill']; ws['A1'].alignment = e['center']
    ws.row_dimensions[1].height = 28

    ws.merge_cells('A2:E2')
    ws['A2'] = 'Instrucciones: Llenar desde la fila 4. Calificaciones en escala 0-10. NO modificar encabezados.'
    ws['A2'].font = Font(italic=True, color='888888', size=10)
    ws['A2'].alignment = Alignment(horizontal='center')

    for col, h in enumerate(['Número de Cuenta', 'Nombre Completo', 'Heteroevaluación', 'Coevaluación', 'Autoevaluación'], 1):
        cell = ws.cell(row=3, column=col, value=h)
        cell.font = e['header_font']; cell.fill = e['header_fill']
        cell.alignment = e['center']; cell.border = e['border']

    ejemplos = [
        ('2024001', 'García López María', 8.5, 7.0, 9.0),
        ('2024002', 'Hernández Ramírez Juan', 6.0, 7.5, 6.5),
        ('2024003', 'Martínez Torres Ana', 9.0, 8.0, 8.5),
    ]
    alt = PatternFill(start_color='f0f7ff', end_color='f0f7ff', fill_type='solid')
    for i, (nc, nombre, h, c, a) in enumerate(ejemplos, 4):
        fill = alt if i % 2 == 0 else PatternFill(fill_type=None)
        for col, val in enumerate([nc, nombre, h, c, a], 1):
            cell = ws.cell(row=i, column=col, value=val)
            cell.fill = fill; cell.border = e['border']
            cell.alignment = e['center'] if col != 2 else Alignment(vertical='center')

    for i, w in enumerate([20, 40, 18, 15, 18], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="plantilla_calificaciones.xlsx"'
    wb.save(response)
    return response


# ─── Captura Rápida ───────────────────────────────────────────────────────────

def captura_rapida(request):
    grupos = Grupo.objects.select_related('semestre').all()
    grupo_id = request.GET.get('grupo')
    asig_id = request.GET.get('asignatura')
    parcial = request.GET.get('parcial', '1')
    grupo_sel = None
    asig_sel = None
    alumnos = []
    asignaturas = []
    alumno_ids_json = '[]'

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        asignaturas = list(Asignatura.objects.filter(semestre=grupo_sel.semestre))

        if asig_id:
            asig_sel = get_object_or_404(Asignatura, id=asig_id)
            alumnos_qs = Alumno.objects.filter(grupo=grupo_sel).order_by('nombre_completo')
            for al in alumnos_qs:
                try:
                    cal = Calificacion.objects.get(alumno=al, asignatura=asig_sel, parcial=int(parcial))
                    al.cal_existente = cal
                except Calificacion.DoesNotExist:
                    al.cal_existente = None
                alumnos.append(al)
            import json as _json
            alumno_ids_json = _json.dumps([a.id for a in alumnos])

    context = {
        'grupos': grupos, 'grupo_sel': grupo_sel, 'asig_sel': asig_sel,
        'asignaturas': asignaturas, 'alumnos': alumnos, 'parcial': parcial,
        'alumno_ids_json': alumno_ids_json,
    }
    return render(request, 'escolar/captura_rapida.html', context)


def guardar_calificaciones(request):
    """AJAX endpoint para guardar calificaciones desde captura rápida."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'})
    import json as _json
    try:
        data = _json.loads(request.body)
        asig = get_object_or_404(Asignatura, id=data['asignatura_id'])
        parcial = int(data['parcial'])
        creados = 0; actualizados = 0

        for row in data.get('rows', []):
            alumno = get_object_or_404(Alumno, id=row['alumno_id'])
            h = float(row['hetero'])
            c = float(row['co'])
            a = float(row['auto'])
            if not (0 <= h <= 10 and 0 <= c <= 10 and 0 <= a <= 10):
                continue
            _, created = Calificacion.objects.update_or_create(
                alumno=alumno, asignatura=asig, parcial=parcial,
                defaults={'heteroevaluacion': h, 'coevaluacion': c, 'autoevaluacion': a}
            )
            if created: creados += 1
            else: actualizados += 1

        return JsonResponse({'ok': True, 'creados': creados, 'actualizados': actualizados})
    except Exception as ex:
        return JsonResponse({'ok': False, 'error': str(ex)})


# ─── Seguimiento ─────────────────────────────────────────────────────────────

def seguimiento(request):
    from .models import Nota
    grupos = Grupo.objects.select_related('semestre').all()
    grupo_id = request.GET.get('grupo')
    grupo_sel = None
    alumnos = list(Alumno.objects.select_related('grupo').prefetch_related('notas', 'calificaciones').all())

    if grupo_id:
        grupo_sel = get_object_or_404(Grupo, id=grupo_id)
        alumnos = [a for a in alumnos if a.grupo_id == int(grupo_id)]

    # Calcular promedio general para cada alumno
    for al in alumnos:
        cals = list(al.calificaciones.all())
        al.promedio_general = round(sum(c.promedio for c in cals) / len(cals), 2) if cals else None

    context = {'grupos': grupos, 'grupo_sel': grupo_sel, 'alumnos': alumnos}
    return render(request, 'escolar/seguimiento.html', context)


def seguimiento_notas_api(request):
    from .models import Nota
    alumno_id = request.GET.get('alumno_id')
    alumno = get_object_or_404(Alumno, id=alumno_id)
    cals = list(alumno.calificaciones.all())
    prom = round(sum(c.promedio for c in cals) / len(cals), 2) if cals else None
    notas = list(Nota.objects.filter(alumno=alumno).values('tipo', 'texto', 'fecha'))
    for n in notas:
        n['fecha'] = n['fecha'].strftime('%d/%m/%Y %H:%M')
    return JsonResponse({'ok': True, 'notas': notas, 'promedio': prom})


def agregar_nota_api(request):
    from .models import Nota
    import json as _json
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    data = _json.loads(request.body)
    alumno = get_object_or_404(Alumno, id=data['alumno_id'])
    Nota.objects.create(alumno=alumno, tipo=data.get('tipo', 'observacion'), texto=data.get('texto', ''))
    return JsonResponse({'ok': True})


# ─── Profesor panel actualizado ──────────────────────────────────────────────

def profesor_panel(request):
    semestres = Semestre.objects.all()
    grupos = Grupo.objects.select_related('semestre').all()
    asignaturas = Asignatura.objects.select_related('semestre').all()
    pasos_flujo = [
        ('Descarga la plantilla Excel', 'Formato predefinido con las columnas correctas'),
        ('Llena con los datos del grupo', 'Número de cuenta, nombre y las 3 evaluaciones'),
        ('Selecciona grupo, asignatura y parcial', 'El sistema sabe a qué registro corresponde'),
        ('Haz clic en Importar Ahora', 'Los alumnos y calificaciones se crean automáticamente'),
    ]
    context = {
        'semestres': semestres, 'grupos': grupos,
        'asignaturas': asignaturas, 'pasos_flujo': pasos_flujo,
    }
    return render(request, 'escolar/profesor.html', context)


def crear_estructura(request):
    if request.method != 'POST':
        return redirect('profesor')
    accion = request.POST.get('accion')

    if accion == 'semestre':
        nombre = request.POST.get('nombre_semestre', '').strip()
        anio = request.POST.get('anio', '')
        periodo = request.POST.get('periodo', '')
        if nombre and anio and periodo:
            sem, created = Semestre.objects.get_or_create(nombre=nombre, anio=int(anio), periodo=periodo)
            messages.success(request, f'✅ Semestre "{sem.nombre}" {"creado" if created else "ya existía"}.')
        else:
            messages.error(request, 'Completa todos los campos del semestre.')

    elif accion == 'grupo':
        sem_id = request.POST.get('semestre_grupo')
        nombre_g = request.POST.get('nombre_grupo', '').strip()
        if sem_id and nombre_g:
            sem = get_object_or_404(Semestre, id=sem_id)
            g, created = Grupo.objects.get_or_create(nombre=nombre_g, semestre=sem)
            messages.success(request, f'✅ Grupo "{g.nombre}" {"creado" if created else "ya existía"}.')
        else:
            messages.error(request, 'Selecciona semestre y nombre de grupo.')

    elif accion == 'asignatura':
        sem_id = request.POST.get('semestre_asig')
        nombre_a = request.POST.get('nombre_asig', '').strip()
        clave_a = request.POST.get('clave_asig', '').strip().upper()
        if sem_id and nombre_a and clave_a:
            sem = get_object_or_404(Semestre, id=sem_id)
            a, created = Asignatura.objects.get_or_create(clave=clave_a, defaults={'nombre': nombre_a, 'semestre': sem})
            messages.success(request, f'✅ Asignatura "{a.nombre}" ({a.clave}) {"creada" if created else "ya existía"}.')
        else:
            messages.error(request, 'Completa todos los campos de la asignatura.')

    elif accion == 'alumno':
        num_cuenta = request.POST.get('numero_cuenta', '').strip()
        nombre = request.POST.get('nombre_alumno', '').strip()
        email = request.POST.get('email_alumno', '').strip()
        grupo_id = request.POST.get('grupo_alumno')
        if num_cuenta and nombre and grupo_id:
            grupo = get_object_or_404(Grupo, id=grupo_id)
            al, created = Alumno.objects.get_or_create(
                numero_cuenta=num_cuenta,
                defaults={'nombre_completo': nombre, 'email': email or f'{num_cuenta}@ithi.edu.mx', 'grupo': grupo}
            )
            if not created:
                messages.warning(request, f'⚠ El alumno {num_cuenta} ya estaba registrado.')
            else:
                messages.success(request, f'✅ Alumno "{al.nombre_completo}" agregado exitosamente.')
        else:
            messages.error(request, 'Completa número de cuenta, nombre y grupo.')

    return redirect('profesor')
