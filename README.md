# Pulso IA

Pulso IA es un pipeline serverless en AWS que descubre noticias de IA, las filtra con Bedrock y publica automáticamente en un canal de Telegram.

## Arquitectura

Flujo principal (Step Functions):

1. `fetch_sources` (Lambda)
   - Fuentes: ArXiv, Product Hunt, GitHub Trending y RSS.
   - Deduplica por `item_id` en DynamoDB.
2. `filter_ai_news` (Lambda)
   - Clasifica y puntúa relevancia con `amazon.nova-lite-v1:0` (Bedrock).
   - Guarda resultados en DynamoDB.
3. `publish_telegram` (Lambda)
   - Publica noticias relevantes en Telegram.
   - Marca `telegram_sent` para evitar reenvíos.

Infraestructura AWS (Terraform):

- DynamoDB: `pulso-ia_items`
- 3 Lambdas + 1 Layer
- Step Functions: `pulso-ia-pipeline`
- EventBridge Scheduler: ejecución cada 1 hora
- CloudWatch Logs
- SSM Parameter Store para secretos

## Estructura del proyecto

```text
lambdas/
  fetch_sources/
  filter_ai_news/
  publish_telegram/
shared/
infra/
scripts/
tests/
```

## Requisitos

- Python 3.12+
- Terraform 1.14+
- AWS CLI v2
- Cuenta AWS con permisos para Lambda, IAM, DynamoDB, Step Functions, Scheduler, SSM y Bedrock
- Bot de Telegram + canal

## Configuración inicial

### 1) Variables operativas

- AWS region: `us-east-1`
- Modelo Bedrock: `amazon.nova-lite-v1:0`

### 2) Secrets en SSM Parameter Store

Crear en `us-east-1`:

- `/pulso-ia/telegram-bot-token`
- `/pulso-ia/producthunt-token`
- `/pulso-ia/github-token`

Ejemplo:

```bash
aws ssm put-parameter \
  --name "/pulso-ia/telegram-bot-token" \
  --value "<TOKEN>" \
  --type "SecureString" \
  --region us-east-1
```

## Deploy

### 1) Empaquetar dependencias de Lambdas

> Recomendado antes de `terraform apply`, para incluir dependencias en los ZIP.

```bash
python3 -m pip install -r lambdas/fetch_sources/requirements.txt -t lambdas/fetch_sources
python3 -m pip install -r lambdas/filter_ai_news/requirements.txt -t lambdas/filter_ai_news
python3 -m pip install -r lambdas/publish_telegram/requirements.txt -t lambdas/publish_telegram

# Copiar módulos compartidos al paquete de cada lambda
cp shared/models.py lambdas/fetch_sources/models.py
cp shared/dynamo.py lambdas/fetch_sources/dynamo.py
cp shared/models.py lambdas/filter_ai_news/models.py
cp shared/dynamo.py lambdas/filter_ai_news/dynamo.py
cp shared/models.py lambdas/publish_telegram/models.py
cp shared/dynamo.py lambdas/publish_telegram/dynamo.py
```

### 2) Provisionar infraestructura

```bash
cd infra
AWS_PROFILE=<tu_profile> terraform init
AWS_PROFILE=<tu_profile> terraform plan -out=tfplan
AWS_PROFILE=<tu_profile> terraform apply tfplan
```

## Pruebas

### Unit tests

```bash
python3 -m pytest -q
```

### Prueba de `fetch_sources`

```bash
AWS_PROFILE=<tu_profile> aws lambda invoke \
  --function-name pulso-ia-fetch-sources \
  --payload '{"sources":["arxiv","rss"],"lookback_hours":2}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/fetch_result.json \
  --region us-east-1

python3 -m json.tool /tmp/fetch_result.json
```

### Prueba de `filter_ai_news`

```bash
ITEMS=$(cat /tmp/fetch_result.json)
AWS_PROFILE=<tu_profile> aws lambda invoke \
  --function-name pulso-ia-filter-ai-news \
  --payload "$ITEMS" \
  --cli-binary-format raw-in-base64-out \
  /tmp/filter_result.json \
  --region us-east-1

python3 -m json.tool /tmp/filter_result.json
```

### Prueba de `publish_telegram` (1 ítem)

```bash
python3 - <<'PY'
import json
with open('/tmp/filter_result.json') as f:
    data = json.load(f)
with open('/tmp/publish_payload.json', 'w') as f:
    json.dump({'relevant_items': data.get('relevant_items', [])[:1]}, f)
PY

AWS_PROFILE=<tu_profile> aws lambda invoke \
  --function-name pulso-ia-publish-telegram \
  --payload file:///tmp/publish_payload.json \
  --cli-binary-format raw-in-base64-out \
  /tmp/publish_result.json \
  --region us-east-1

python3 -m json.tool /tmp/publish_result.json
```

## Carga inicial (10 días)

```bash
AWS_PROFILE=<tu_profile> AWS_REGION=us-east-1 python3 scripts/initial_run.py
```

## Operación

- Scheduler: `pulso-ia-hourly` — por defecto **cada 15 minutos** (`pipeline_schedule_expression` en Terraform).
- State machine: `pulso-ia-pipeline` (si no hay ítems nuevos en el fetch, igual corre **publish** para drenar la cola en Dynamo).
- Tabla DynamoDB: `pulso-ia_items` — `telegram_sent`: `false` | `queued` (pendiente de publicar) | `true`.

CloudWatch logs:

- `/aws/lambda/pulso-ia-fetch-sources`
- `/aws/lambda/pulso-ia-filter-ai-news`
- `/aws/lambda/pulso-ia-publish-telegram`

## Troubleshooting

### `No module named 'feedparser'` o similares

Las dependencias no quedaron empaquetadas en la Lambda.
Repetir instalación con `pip install -t` en cada carpeta `lambdas/*` y volver a aplicar Terraform.

### `No module named 'models'`

Faltan módulos compartidos dentro del paquete de Lambda.
Copiar `shared/models.py` y `shared/dynamo.py` a cada Lambda y reaplicar.

### `Malformed input request ... extraneous key [max_tokens]`

Bedrock rechaza payload de `invoke_model` para Nova.
Usar `bedrock.converse(...)` con `inferenceConfig.maxTokens`.

### `Type mismatch for Index Key telegram_sent Expected: S Actual: BOOL`

El campo `telegram_sent` en DynamoDB/GSI debe ser string (`"false"`/`"true"`).

## Seguridad

- No hardcodear tokens en código ni en commits.
- Usar SSM SecureString.
- Limitar permisos IAM por principio de mínimo privilegio.

## Roadmap sugerido

- Mejorar ranking/threshold por categoría y fuente.
- Agregar control de tasa y anti-duplicados avanzados en Telegram.
- Añadir CI (tests + validación Terraform).
- Soportar nuevas fuentes con plugin en `lambdas/fetch_sources/sources`.
