# Ficha técnica del pronóstico escolar

## Identificación

- Nombre: extrapolación lineal de calificaciones parciales.
- Versión: `tendencia-lineal-v1`.
- Salida: estimación orientativa del promedio del parcial 3, acotada al intervalo 0–10.

## Variables y método

El modelo utiliza exclusivamente los promedios observados del mismo alumno y asignatura en P1 y P2:

`estimación P3 = (-1 × promedio P1) + (2 × promedio P2)`

La explicación presenta, para cada variable, el valor observado, su coeficiente y la contribución `coeficiente × valor`. Sin ambos parciales no se genera una estimación. Los promedios se calculan a partir de las calificaciones registradas para el parcial.

No se utilizan género, edad, origen, discapacidad, nivel socioeconómico ni otros atributos personales como variables del modelo. La entrada se construye explícitamente a partir de los promedios de P1 y P2.

## Métricas

R² y MAE se calculan retrospectivamente con las series del grupo que tienen P1, P2 y P3 observados. Para cada serie se estima P3 con P1 y P2 y se compara la estimación con el P3 observado. R² no se informa (N/D) si hay menos de dos muestras o si los valores observados no tienen variación. MAE se expresa en puntos de calificación.

Las métricas corresponden al grupo seleccionado y a las muestras históricas disponibles; no representan una validación externa ni garantizan desempeño futuro. La cantidad de muestras se muestra junto a las métricas.

## Limitaciones y uso responsable

- Es una extrapolación lineal simple con dos observaciones, sensible a cambios abruptos y a datos faltantes.
- La salida se limita a 0–10; si la extrapolación sin límite cae fuera del intervalo, la cifra mostrada se recorta.
- Con pocas muestras, R² puede no estar definido y las métricas pueden ser inestables.
- El pronóstico no sustituye la revisión docente, no determina aprobación/reprobación y nunca cambia automáticamente calificaciones ni estatus académico.
- Alumnos, madres, padres y tutores pueden solicitar revisión humana; coordinación registra estado, responsable, respuesta y bitácora.
