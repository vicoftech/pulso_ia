## Context

En `shared/dynamo.py` existe `batch_get_existing_ids()` que detecta ítems duplicados por `item_id`.
Sin embargo, no existe un mecanismo para detectar noticias que son "el mismo evento" aunque provengan
de fuentes distintas (por ejemplo, el mismo lanzamiento de OpenAI aparece en TechCrunch Y en VentureBeat).

El resultado: el mismo evento se publica dos veces en el canal de Telegram con horas de diferencia.

## Task

Agregar una función `find_near_duplicate_ids(items: list[RawNewsItem]) -> set[str]` en `shared/dynamo.py`
que, dado un lote de ítems nuevos, detecte cuáles son near-duplicados entre sí por título similar.

Criterio de similitud: si dos ítems comparten más del 60% de las palabras significativas del título
(ignorando stopwords en español e inglés: el, la, los, las, de, del, en, y, a, the, of, for, with, and, or, is, to),
se considera near-duplicado. Conservar solo el ítem con `published_at` más reciente del par.

Integrar en `lambdas/fetch_sources/handler.py`: después del filtro de `existing_ids`,
aplicar `find_near_duplicate_ids` y excluir los near-duplicados antes de retornar.

## Acceptance Criteria

- [ ] `find_near_duplicate_ids(items)` retorna un `set[str]` de `item_id`s a descartar
- [ ] Dos títulos con >60% de palabras significativas en común se consideran near-duplicados
- [ ] En cada par near-duplicado se descarta el ítem más antiguo (menor `published_at`)
- [ ] Si `published_at` es igual, se descarta el que aparece segundo en la lista
- [ ] La función no hace llamadas a AWS (es lógica pura)
- [ ] `fetch_sources/handler.py` aplica el filtro antes de retornar `new_items`
- [ ] Tests en `tests/test_near_duplicates.py` con al menos 4 casos:
  - Títulos idénticos → descarta el más antiguo
  - Títulos con >60% similitud → descarta el más antiguo  
  - Títulos con <60% similitud → no descarta ninguno
  - Lista con un solo ítem → retorna set vacío

## Technical Constraints

- Solo modificar: `shared/dynamo.py`, `lambdas/fetch_sources/handler.py`, agregar `tests/test_near_duplicates.py`
- Sin dependencias externas (no usar `difflib`, `fuzzywuzzy` ni similares — implementar con sets de Python)
- La función debe ser O(n²) en el peor caso, aceptable para lotes de hasta 50 ítems (MAX_ITEMS actual)
- No cambiar la firma pública de ninguna función existente en `dynamo.py`

## Out of scope

- No comparar contra ítems ya en DynamoDB (solo dentro del lote actual)
- No implementar similitud semántica con embeddings
- No modificar el Step Functions ni el esquema de DynamoDB
