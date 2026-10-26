"""Cálculo explicable y evaluación histórica de pronósticos escolares."""

from statistics import mean


VERSION_MODELO = 'tendencia-lineal-v1'


def explicar_pronostico(promedios):
    """Extrapola el parcial 3 desde P1 y P2 y expone su contribución lineal."""
    if 1 not in promedios or 2 not in promedios:
        return None

    p1 = float(promedios[1])
    p2 = float(promedios[2])
    raw_prediction = -p1 + 2 * p2
    prediction = round(max(0, min(10, raw_prediction)), 2)
    slope = p2 - p1

    return {
        'pronostico': prediction,
        'tendencia': 'sube' if slope > 0.2 else ('baja' if slope < -0.2 else 'estable'),
        'factores': [
            {
                'variable': 'Promedio P1',
                'valor': round(p1, 2),
                'coeficiente': -1,
                'contribucion': round(-p1, 2),
            },
            {
                'variable': 'Promedio P2',
                'valor': round(p2, 2),
                'coeficiente': 2,
                'contribucion': round(2 * p2, 2),
            },
        ],
        'entradas': {'P1': round(p1, 2), 'P2': round(p2, 2)},
        'version_modelo': VERSION_MODELO,
    }


def metricas_validacion(evaluaciones):
    """Calcula R² y MAE de predicciones P3 comparadas con P3 observado."""
    if not evaluaciones:
        return {'r2': None, 'mae': None, 'muestras': 0}

    errores = [
        float(predicho) - float(observado)
        for predicho, observado in evaluaciones
    ]
    mae = round(mean(abs(error) for error in errores), 3)
    observados = [float(observado) for _, observado in evaluaciones]
    promedio_observado = mean(observados)
    suma_total = sum((valor - promedio_observado) ** 2 for valor in observados)
    if len(evaluaciones) < 2 or suma_total == 0:
        r2 = None
    else:
        r2 = round(1 - sum(error ** 2 for error in errores) / suma_total, 3)
    return {'r2': r2, 'mae': mae, 'muestras': len(evaluaciones)}
