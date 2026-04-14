terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Estado remoto S3: añadí dentro de este bloque terraform { ... }:
  #   backend "s3" {}
  # y ejecutá terraform init -backend-config=backend.hcl (-migrate-state si venís de state local).
  # Ver backend.hcl.example. Sin ese bloque, el state es local (útil para import en una máquina nueva).
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  default = "us-east-1"
}

variable "project_name" {
  default = "pulso-ia"
}

variable "max_items_stored" {
  default = "100"
}

variable "relevance_threshold" {
  default = "60"
}

variable "pipeline_schedule_expression" {
  description = "Cadencia entre barridos del pipeline (EventBridge Scheduler). Por defecto 15 minutos."
  type        = string
  default     = "rate(15 minutes)"
}

variable "telegram_channel_id" {
  default = "-1003846999541"
}

variable "link_public_base_url" {
  description = <<-EOT
    Base HTTPS pública: enlaces en botones (/p/, /r/) y, si aplicas certificado, el mismo host en API Gateway.
    Ej.: https://news.workium.ai — debe coincidir con el nombre del certificado ACM.
  EOT
  type    = string
  default = "https://news.workium.ai"
}

variable "api_gateway_custom_domain_certificate_arn" {
  description = <<-EOT
    ARN del certificado ACM en la MISMA región que el API (p. ej. us-east-1) para el host de link_public_base_url
    (news.workium.ai o *.workium.ai). Vacío = sin dominio custom; usá el endpoint execute-api y un CNAME manual si querés.
  EOT
  type    = string
  default = ""
}

locals {
  common_env = {
    DYNAMODB_TABLE   = aws_dynamodb_table.items.name
    MAX_ITEMS_STORED = var.max_items_stored
    AWS_REGION_NAME  = var.aws_region
    LOG_LEVEL        = "INFO"
  }
}

resource "aws_dynamodb_table" "items" {
  name         = "${var.project_name}_items"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "item_id"

  attribute {
    name = "item_id"
    type = "S"
  }

  attribute {
    name = "telegram_sent"
    type = "S"
  }

  attribute {
    name = "processed_at"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  global_secondary_index {
    name            = "telegram_sent-processed_at-index"
    hash_key        = "telegram_sent"
    range_key       = "processed_at"
    projection_type = "ALL"
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_dynamodb_table" "events" {
  name         = "${var.project_name}-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"

  attribute {
    name = "event_id"
    type = "S"
  }
  attribute {
    name = "item_id"
    type = "S"
  }
  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "event_name"
    type = "S"
  }
  attribute {
    name = "occurred_at"
    type = "S"
  }

  global_secondary_index {
    name            = "user_id-occurred_at-index"
    hash_key        = "user_id"
    range_key       = "occurred_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "item_id-occurred_at-index"
    hash_key        = "item_id"
    range_key       = "occurred_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "event_name-occurred_at-index"
    hash_key        = "event_name"
    range_key       = "occurred_at"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_dynamodb_table" "short_links" {
  name         = "${var.project_name}-short-links"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "slug"

  attribute {
    name = "slug"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Project = var.project_name
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fetch" {
  name               = "${var.project_name}-fetch-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "filter" {
  name               = "${var.project_name}-filter-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role" "publish" {
  name               = "${var.project_name}-publish-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "fetch_policy" {
  role = aws_iam_role.fetch.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem"]
        Resource = aws_dynamodb_table.items.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/pulso-ia/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "filter_policy" {
  role = aws_iam_role.filter.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:BatchWriteItem", "dynamodb:BatchGetItem"]
        Resource = aws_dynamodb_table.items.arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "publish_policy" {
  role = aws_iam_role.publish.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [aws_dynamodb_table.items.arn, "${aws_dynamodb_table.items.arn}/index/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.short_links.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/pulso-ia/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

# Lambda Python layers must use python/lib/<runtime>/site-packages/ so imports resolve.
data "archive_file" "shared_layer" {
  type        = "zip"
  output_path = "${path.module}/../dist/shared_layer.zip"

  source {
    content  = file("${path.module}/../shared/dynamo.py")
    filename = "python/lib/python3.12/site-packages/dynamo.py"
  }
  source {
    content  = file("${path.module}/../shared/models.py")
    filename = "python/lib/python3.12/site-packages/models.py"
  }
}

resource "aws_lambda_layer_version" "shared" {
  filename            = data.archive_file.shared_layer.output_path
  layer_name          = "${var.project_name}-shared"
  source_code_hash    = data.archive_file.shared_layer.output_base64sha256
  compatible_runtimes = ["python3.12"]
}

data "archive_file" "fetch" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/fetch_sources"
  output_path = "${path.module}/../dist/fetch_sources.zip"
}

resource "aws_lambda_function" "fetch" {
  filename         = data.archive_file.fetch.output_path
  function_name    = "${var.project_name}-fetch-sources"
  role             = aws_iam_role.fetch.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256
  source_code_hash = data.archive_file.fetch.output_base64sha256
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = local.common_env
  }
}

data "archive_file" "filter" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/filter_ai_news"
  output_path = "${path.module}/../dist/filter_ai_news.zip"
}

resource "aws_lambda_function" "filter" {
  filename         = data.archive_file.filter.output_path
  function_name    = "${var.project_name}-filter-ai-news"
  role             = aws_iam_role.filter.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 180
  memory_size      = 256
  source_code_hash = data.archive_file.filter.output_base64sha256
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = merge(local.common_env, {
      BEDROCK_MODEL_ID    = "amazon.nova-lite-v1:0"
      RELEVANCE_THRESHOLD = var.relevance_threshold
      BATCH_SIZE          = "20"
    })
  }
}

data "archive_file" "publish" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/publish_telegram"
  output_path = "${path.module}/../dist/publish_telegram.zip"
}

resource "aws_lambda_function" "publish" {
  filename         = data.archive_file.publish.output_path
  function_name    = "${var.project_name}-publish-telegram"
  role             = aws_iam_role.publish.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 128
  source_code_hash = data.archive_file.publish.output_base64sha256
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = merge(local.common_env, {
      TELEGRAM_CHANNEL_ID = var.telegram_channel_id
      PUBLIC_LINK_BASE    = local.link_public_base
      SHORT_LINKS_TABLE   = aws_dynamodb_table.short_links.name
    })
  }
}

resource "aws_cloudwatch_log_group" "fetch" {
  name              = "/aws/lambda/${aws_lambda_function.fetch.function_name}"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "filter" {
  name              = "/aws/lambda/${aws_lambda_function.filter.function_name}"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "publish" {
  name              = "/aws/lambda/${aws_lambda_function.publish.function_name}"
  retention_in_days = 7
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.project_name}-sfn-role"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

resource "aws_iam_role_policy" "sfn_policy" {
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.fetch.arn, aws_lambda_function.filter.arn, aws_lambda_function.publish.arn]
    }]
  })
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.project_name}-pipeline"
  role_arn = aws_iam_role.sfn.arn

  definition = jsonencode({
    Comment = "Pulso IA - AI News Pipeline"
    StartAt = "FetchSources"
    States = {
      FetchSources = {
        Type       = "Task"
        Resource   = aws_lambda_function.fetch.arn
        ResultPath = "$.fetch_result"
        Next       = "HasItems"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          MaxAttempts     = 2
          IntervalSeconds = 30
        }]
      }
      HasItems = {
        Type = "Choice"
        Choices = [{
          Variable           = "$.fetch_result.count"
          NumericGreaterThan = 0
          Next               = "FilterAINews"
        }]
        Default = "QueueDrainOnly"
      }
      QueueDrainOnly = {
        Type       = "Pass"
        Result = {
          relevant_items  = []
          total_processed = 0
          total_relevant  = 0
          by_category     = {}
        }
        ResultPath = "$.filter_result"
        Next       = "PublishTelegram"
      }
      FilterAINews = {
        Type       = "Task"
        Resource   = aws_lambda_function.filter.arn
        InputPath  = "$.fetch_result"
        ResultPath = "$.filter_result"
        Next       = "PublishTelegram"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          MaxAttempts     = 2
          IntervalSeconds = 30
        }]
      }
      PublishTelegram = {
        Type      = "Task"
        Resource  = aws_lambda_function.publish.arn
        InputPath = "$.filter_result"
        Next      = "Done"
      }
      Done = {
        Type = "Succeed"
      }
    }
  })
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project_name}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler_policy" {
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = aws_sfn_state_machine.pipeline.arn
    }]
  })
}

resource "aws_scheduler_schedule" "hourly" {
  name = "${var.project_name}-hourly"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = var.pipeline_schedule_expression

  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      sources        = ["arxiv", "producthunt", "github", "rss"]
      lookback_hours = 1
      initial_run    = false
    })
  }
}

data "archive_file" "engagement" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/engagement_handler"
  output_path = "${path.module}/../dist/engagement_handler.zip"
}

resource "aws_iam_role" "engagement" {
  name               = "${var.project_name}-engagement-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "engagement_basic" {
  role       = aws_iam_role.engagement.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "engagement_policy" {
  role = aws_iam_role.engagement.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.items.arn,
          "${aws_dynamodb_table.items.arn}/index/*",
          aws_dynamodb_table.short_links.arn,
        ]
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.events.arn,
          "${aws_dynamodb_table.events.arn}/index/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/pulso-ia/*"
      }
    ]
  })
}

resource "aws_lambda_function" "engagement" {
  filename         = data.archive_file.engagement.output_path
  function_name    = "${var.project_name}-engagement-handler"
  role             = aws_iam_role.engagement.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  source_code_hash = data.archive_file.engagement.output_base64sha256
  environment {
    variables = {
      DYNAMODB_TABLE    = aws_dynamodb_table.items.name
      EVENTS_TABLE      = aws_dynamodb_table.events.name
      SHORT_LINKS_TABLE = aws_dynamodb_table.short_links.name
      LOG_LEVEL         = "INFO"
      PUBLIC_LINK_BASE  = local.link_public_base
    }
  }
}

resource "aws_cloudwatch_log_group" "engagement" {
  name              = "/aws/lambda/${aws_lambda_function.engagement.function_name}"
  retention_in_days = 7
}

resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.project_name}-webhook"
  protocol_type = "HTTP"
}

locals {
  public_api_base        = trimsuffix(aws_apigatewayv2_api.webhook.api_endpoint, "/")
  link_public_base       = trimsuffix(var.link_public_base_url, "/")
  custom_domain_hostname = replace(replace(trim(var.link_public_base_url, "/"), "https://", ""), "http://", "")
}

resource "aws_apigatewayv2_integration" "engagement" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.engagement.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.engagement.id}"
}

resource "aws_apigatewayv2_route" "article_redirect" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /r/{item_id}"
  target    = "integrations/${aws_apigatewayv2_integration.engagement.id}"
}

resource "aws_apigatewayv2_route" "short_redirect" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /p/{slug}"
  target    = "integrations/${aws_apigatewayv2_integration.engagement.id}"
}

resource "aws_apigatewayv2_stage" "webhook_default" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_engagement" {
  statement_id  = "AllowAPIGatewayEngagement"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.engagement.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

resource "aws_apigatewayv2_domain_name" "public" {
  count       = var.api_gateway_custom_domain_certificate_arn != "" ? 1 : 0
  domain_name = local.custom_domain_hostname

  domain_name_configuration {
    certificate_arn = var.api_gateway_custom_domain_certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }

  tags = {
    Project = var.project_name
  }
}

resource "aws_apigatewayv2_api_mapping" "public" {
  count       = var.api_gateway_custom_domain_certificate_arn != "" ? 1 : 0
  api_id      = aws_apigatewayv2_api.webhook.id
  domain_name = aws_apigatewayv2_domain_name.public[0].id
  stage       = aws_apigatewayv2_stage.webhook_default.name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "dynamodb_table" {
  value = aws_dynamodb_table.items.name
}

output "events_table" {
  value = aws_dynamodb_table.events.name
}

output "webhook_url" {
  value       = "${local.public_api_base}/webhook"
  description = "URL del API real (execute-api). Sirve siempre; usala para setWebhook si el dominio custom aún no resuelve."
}

output "webhook_url_custom_domain" {
  value       = var.api_gateway_custom_domain_certificate_arn != "" ? "${local.link_public_base}/webhook" : null
  description = "https://news.workium.ai/webhook (solo si creaste dominio custom en Terraform). Registrá esta URL en Telegram cuando el DNS ya apunte bien."
}

output "api_gateway_base" {
  value       = local.public_api_base
  description = "Host real de API Gateway (webhook y pruebas hasta mapear dominio)."
}

output "article_redirect_base" {
  value = "${local.link_public_base}/r/"
}

output "short_link_base" {
  value = "${local.link_public_base}/p/"
}

output "link_public_base" {
  value = local.link_public_base
}

output "short_links_table" {
  value = aws_dynamodb_table.short_links.name
}

output "custom_domain_target_domain" {
  value       = try(aws_apigatewayv2_domain_name.public[0].domain_name_configuration[0].target_domain_name, null)
  description = "Valor CNAME: apuntá news.workium.ai → este host (regional execute-api)."
}

output "custom_domain_hosted_zone_id" {
  value       = try(aws_apigatewayv2_domain_name.public[0].domain_name_configuration[0].hosted_zone_id, null)
  description = "Hosted zone ID de API Gateway (solo si usás alias Route53 en vez de CNAME)."
}
