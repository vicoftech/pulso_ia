# CLAUDE.md — Pulso IA

Convenciones del proyecto para el agente de IA (Claude Code).
Este archivo se carga automáticamente en cada ejecución del pipeline.

---

## Arquitectura general

Pipeline serverless en AWS que descubre, filtra y publica noticias de IA en Telegram.

```
EventBridge (cada 1h)
  → Step Functions: pulso-ia-pipeline
      → fetch_sources   (Lambda)  — fetches de ArXiv, ProductHunt, GitHub, RSS
      → filter_ai_news  (Lambda)  — clasifica y puntúa con Amazon Bedrock
      → publish_telegram (Lambda) — publica en Telegram con formato MarkdownV2
```

## Stack

- **Lenguaje**: Python 3.12
- **Infra**: AWS Lambda + Step Functions + DynamoDB + Terraform
- **Modelo IA**: Amazon Bedrock (`amazon.nova-lite-v1:0`)
- **Tests**: pytest (sin dependencias de AWS reales — usar mocks/monkeypatch)
- **Linter**: ninguno configurado aún (no bloquear CI por esto)

## Estructura de carpetas

```
lambdas/
  fetch_sources/       — handler.py + sources/ + requirements.txt
  filter_ai_news/      — handler.py + requirements.txt
  publish_telegram/    — handler.py + like_counts.py + requirements.txt
  evening_summary/     — handler.py
  engagement_handler/  — handler.py
  telegram_engagement/ — handler.py + like_counts.py
shared/
  dynamo.py            — acceso a DynamoDB (batch_get, batch_save, mark_as_sent)
  models.py            — RawNewsItem, ProcessedNewsItem (dataclasses)
  outbound_url.py      — build_open_and_track_url, build_workium_r_url
  og_image.py          — extracción de og:image
  engagement.py        — helpers de engagement
  like_counts.py       — conteo de likes
infra/                 — Terraform (no modificar salvo spec explícito)
tests/                 — pytest, un archivo por módulo
scripts/               — utilidades CLI (no son lambdas)
sources/
  sources.json         — catálogo de fuentes RSS/API
skills/
  pulso-ia-filter-llm.md — convenciones del prompt de filtrado
specs/                 — tickets .md del pipeline de IA (NO tocar estos archivos)
```

## Reglas de implementación

### General
- Modificar solo los archivos mencionados en el spec. No tocar `infra/` salvo que el spec lo indique explícitamente.
- No agregar dependencias externas sin incluirlas en el `requirements.txt` de la lambda correspondiente.
- Las lambdas hacen `sys.path.insert` para acceder a `shared/`. No cambiar ese patrón.
- Variables de entorno: nunca hardcodear valores de prod. Usar `os.environ.get("VAR", "default")`.

### Tests
- Cada lambda tiene su propio archivo de test en `tests/test_<nombre>.py`.
- Usar `monkeypatch` para mockear llamadas a AWS (boto3, SSM, DynamoDB, Bedrock).
- `conftest.py` ya define `DYNAMODB_TABLE` y `TELEGRAM_CHANNEL_ID` como env vars de test.
- Los tests deben correr sin credenciales AWS reales (`pytest` puro, sin `moto` salvo que ya esté en uso).
- Cobertura mínima esperada: cubrir el happy path y al menos un caso de error por función.

### Modelos de datos
- `RawNewsItem`: item crudo de una fuente. `item_id` se genera como MD5 de la URL.
- `ProcessedNewsItem`: item procesado con clasificación Bedrock y campos de publicación.
- No agregar campos a los dataclasses sin actualizar `_item_to_dynamo` en `dynamo.py`.

### Telegram
- El formato de mensajes usa MarkdownV2. Escapar con `escape_md2()` en `publish_telegram/handler.py`.
- `CATEGORY_META` define emojis y hashtags por categoría. Mantener consistencia.

### Fuentes
- Para agregar una fuente nueva, crear `lambdas/fetch_sources/sources/<nombre>.py` que herede de `base.py` y registrarse en `sources/__init__.py`.
- Documentar la fuente nueva en `sources/sources.json`.

### Commits
- Formato: `feat(<ticket>): descripción` o `fix(<ticket>): descripción`
- En inglés la acción, en español está permitido en comentarios de código.

## Variables de entorno esperadas en Lambda (referencia)

| Variable                      | Lambda            | Default                    |
|-------------------------------|-------------------|----------------------------|
| `DYNAMODB_TABLE`              | todas             | —                          |
| `BEDROCK_MODEL_ID`            | filter_ai_news    | `amazon.nova-lite-v1:0`    |
| `RELEVANCE_THRESHOLD`         | filter_ai_news    | `60`                       |
| `BATCH_SIZE`                  | filter_ai_news    | `20`                       |
| `TELEGRAM_CHANNEL_ID`         | publish_telegram  | —                          |
| `TIMEZONE`                    | publish_telegram  | `America/Argentina/Buenos_Aires` |
| `PULSO_OUTBOUND_TRACKING_BASE`| publish_telegram  | `https://news.workium.ai`  |
| `LOG_LEVEL`                   | todas             | `INFO`                     |
