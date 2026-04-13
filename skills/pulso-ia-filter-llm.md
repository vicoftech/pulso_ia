# Skills del LLM — filtrado de noticias (Pulso IA)

Este documento describe las **habilidades y reglas** que aplica el modelo de lenguaje al filtrar ítems en la lambda `filter_ai_news` (Amazon Bedrock `converse`). La implementación vive en `lambdas/filter_ai_news/handler.py`.

---

## 1. Rol y contexto

| Skill | Descripción |
|--------|-------------|
| **Curador editorial** | Actúa como curador de IA para el canal premium de Telegram **Pulso IA**. |
| **Criterio estricto** | Solo debe “pasar” noticia que sea **realmente relevante** para el foco del canal; descarta ruido, clickbait genérico y temas tangenciales. |
| **Análisis por lote** | Recibe lotes de ítems (título + fragmento de contenido) y debe mantener **el mismo orden** que la entrada al devolver el JSON. |

---

## 2. Clasificación temática (categorías)

El modelo debe asignar **una** categoría cuando el ítem encaja; si no aplica, usa `null` (el pipeline puede mapear a `UNCATEGORIZED`).

| Categoría | Skill / criterio |
|-----------|------------------|
| **NEW_PRODUCT** | Detectar **nuevo producto, herramienta o servicio** de IA (lanzamiento comercial o anuncio claro). |
| **MODEL_UPDATE** | Detectar **nuevo modelo, versión, capacidades nuevas** o actualizaciones importantes de modelos. |
| **METHODOLOGY** | Detectar **técnicas, papers o enfoques** útiles para quien practica o construye con IA. |
| **MARKET_NEWS** | Detectar **M&A, financiamiento, regulación, lanzamientos mayores del mercado** o fallos relevantes a escala industria. |
| **USE_CASE** | Detectar **aplicaciones novedosas de IA** en dominios concretos (no solo mención superficial de “IA”). |

---

## 3. Juicio por ítem (campos de salida)

Para cada noticia el modelo debe producir un objeto JSON con estas “skills” de decisión:

| Campo | Skill |
|-------|--------|
| **`is_ai_related`** | Determinar si el contenido está **sustancialmente relacionado con inteligencia artificial** (no basta un titular con la palabra “AI”). |
| **`is_relevant`** | Determinar si el ítem **merece el canal Pulso IA**: calidad editorial, novedad y alineación con audiencia técnica/profesional. |
| **`relevance_score`** | Asignar un **puntaje 0–100** coherente con `is_relevant` (el sistema solo publica si además supera el umbral configurado en la lambda, p. ej. `RELEVANCE_THRESHOLD`). |
| **`summary_es`** | Redactar **resumen en español**, máximo **280 caracteres**, directo, sin rodeos, adecuado para card/Telegram. |
| **`item_id`** | **Preservar** el identificador recibido para correlacionar con el ítem original. |

---

## 4. Formato y disciplina de respuesta

| Skill | Regla |
|-------|--------|
| **Solo JSON** | Responder **únicamente** con un array JSON válido; sin markdown, sin explicación, sin texto previo o posterior. |
| **Orden estable** | El array de salida debe estar en el **mismo orden** que el array de entrada. |
| **Estructura fija** | Cada elemento: `item_id`, `is_ai_related`, `is_relevant`, `category`, `relevance_score`, `summary_es`. |

---

## 5. Entrada que recibe el modelo (por ítem)

En cada llamada, por ítem se envía aproximadamente:

- `item_id`
- `title`
- `raw_content` (truncado a **300 caracteres** en código)

El modelo debe inferir categoría y relevancia con esa información limitada.

---

## 6. Comportamiento del sistema (no del LLM)

- **Umbral**: Tras la clasificación, la lambda marca como “relevante para publicar” solo si `is_relevant` es verdadero **y** `relevance_score >= RELEVANCE_THRESHOLD` (por defecto **60**).
- **Errores de Bedrock**: Si falla la llamada o el parseo, el sistema asigna ítems del lote como no relevantes con score 0 y sin resumen.
- **Modelo**: Definido por `BEDROCK_MODEL_ID` (por defecto `amazon.nova-lite-v1:0`).
- **Tamaño de lote**: `BATCH_SIZE` (por defecto **20** ítems por invocación al modelo).

---

## Referencia rápida en código

```26:47:lambdas/filter_ai_news/handler.py
SYSTEM_PROMPT = """You are the AI curator for "Pulso IA", a premium Telegram channel.
Analyze news items and classify them strictly. Only truly relevant AI news passes.

Categories:
- NEW_PRODUCT: New AI tool, service, or product launch
- MODEL_UPDATE: New model release, version update, or new features
- METHODOLOGY: New techniques, papers, or approaches relevant to AI practitioners
- MARKET_NEWS: Acquisitions, funding, major launches, regulatory news, significant failures
- USE_CASE: Novel applications of AI in specific domains

Respond ONLY with a valid JSON array. No markdown, no explanation, no preamble."""
    ...
        'Return format per item:\n'
        '{"item_id":"...","is_ai_related":true/false,"is_relevant":true/false,'
        '"category":"NEW_PRODUCT|MODEL_UPDATE|METHODOLOGY|MARKET_NEWS|USE_CASE|null",'
        '"relevance_score":0-100,'
        '"summary_es":"resumen en espanol, maximo 280 caracteres, directo y sin rodeos"}'
```

---

*Última alineación con el código del repo; si cambian prompts o umbrales, actualizar este archivo.*
