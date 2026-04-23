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
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

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

variable "telegram_channel_id" {
  default = "-1003846999541"
}

# URL del servicio de redirección para contar clics (botón «Leer más»); p. ej. news.workium.ai
variable "outbound_tracking_base" {
  type    = string
  default = "https://news.workium.ai"
}

# Prefijo de path antes del item_id, p. ej. /r → https://host/r/<item_id>
variable "outbound_tracking_path" {
  type    = string
  default = "/r"
}

# Query param (Workium: url)
variable "outbound_tracking_query_param" {
  type    = string
  default = "url"
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

  attribute {
    name = "outbox_key"
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

  global_secondary_index {
    name            = "outbox_key-processed_at-index"
    hash_key        = "outbox_key"
    range_key       = "processed_at"
    projection_type = "ALL"
  }

  tags = {
    Project = var.project_name
  }
}

# Eventos de engagement: aperturas (open) y me gusta (like) para análisis
resource "aws_dynamodb_table" "engagement" {
  name         = "${var.project_name}_engagement"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "item_id"
  range_key    = "event_sk"

  attribute {
    name = "item_id"
    type = "S"
  }

  attribute {
    name = "event_sk"
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

# Contador de me gusta por post (etiqueta del botón; dedup múltiples taps = mismo criterio de analytics)
resource "aws_dynamodb_table" "like_counts" {
  name         = "${var.project_name}_like_counts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "item_id"

  attribute {
    name = "item_id"
    type = "S"
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

resource "aws_iam_role" "telegram_engagement" {
  name               = "${var.project_name}-telegram-engagement-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "telegram_engagement_policy" {
  role = aws_iam_role.telegram_engagement.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.engagement.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:UpdateItem", "dynamodb:GetItem"]
        Resource = [aws_dynamodb_table.like_counts.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/pulso-ia/telegram-bot-token"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "fetch_policy" {
  role = aws_iam_role.fetch.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem"]
        Resource = aws_dynamodb_table.items.arn
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/pulso-ia/*"
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
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
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:BatchWriteItem"]
        Resource = aws_dynamodb_table.items.arn
      },
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
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
        Effect = "Allow"
        Action = ["dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [aws_dynamodb_table.items.arn, "${aws_dynamodb_table.items.arn}/index/*"]
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem"]
        Resource = [aws_dynamodb_table.like_counts.arn]
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/pulso-ia/*"
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

data "archive_file" "shared_layer" {
  type        = "zip"
  source_dir  = "${path.module}/../shared"
  output_path = "${path.module}/../dist/shared_layer.zip"
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

data "archive_file" "telegram_engagement" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/telegram_engagement"
  output_path = "${path.module}/../dist/telegram_engagement.zip"
}

resource "aws_lambda_function" "telegram_engagement" {
  filename         = data.archive_file.telegram_engagement.output_path
  function_name    = "${var.project_name}-telegram-engagement"
  role             = aws_iam_role.telegram_engagement.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 20
  memory_size      = 128
  source_code_hash = data.archive_file.telegram_engagement.output_base64sha256
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = {
      DYNAMODB_ENGAGEMENT_TABLE     = aws_dynamodb_table.engagement.name
      DYNAMODB_LIKE_COUNTS_TABLE      = aws_dynamodb_table.like_counts.name
      LOG_LEVEL                       = "INFO"
      PULSO_OUTBOUND_TRACKING_BASE    = var.outbound_tracking_base
      PULSO_OUTBOUND_TRACKING_PATH    = var.outbound_tracking_path
      PULSO_OUTBOUND_QUERY_PARAM      = var.outbound_tracking_query_param
    }
  }
}

resource "aws_apigatewayv2_api" "engagement" {
  name          = "${var.project_name}-engagement"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "engagement" {
  api_id      = aws_apigatewayv2_api.engagement.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "engagement" {
  api_id                 = aws_apigatewayv2_api.engagement.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.telegram_engagement.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "engagement_get_c" {
  api_id    = aws_apigatewayv2_api.engagement.id
  route_key = "GET /c"
  target    = "integrations/${aws_apigatewayv2_integration.engagement.id}"
}

resource "aws_apigatewayv2_route" "engagement_post_webhook" {
  api_id    = aws_apigatewayv2_api.engagement.id
  route_key = "POST /webhook/telegram"
  target    = "integrations/${aws_apigatewayv2_integration.engagement.id}"
}

resource "aws_lambda_permission" "apigw_invoke_telegram_engagement" {
  statement_id  = "AllowAPIGWInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.telegram_engagement.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:aws:execute-api:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${aws_apigatewayv2_api.engagement.id}/*/*"
}

resource "aws_cloudwatch_log_group" "telegram_engagement" {
  name              = "/aws/lambda/${aws_lambda_function.telegram_engagement.function_name}"
  retention_in_days = 7
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

  # Botón «Leer más» → solo news.workium.ai?url=… (no execute-api; opcional: setear PULSO_OPEN_TRACKER_URL a mano)
  environment {
    variables = merge(local.common_env, {
      TELEGRAM_CHANNEL_ID         = var.telegram_channel_id
      TIMEZONE                    = "America/Argentina/Buenos_Aires"
      PULSO_OUTBOUND_TRACKING_BASE  = var.outbound_tracking_base
      PULSO_OUTBOUND_TRACKING_PATH  = var.outbound_tracking_path
      PULSO_OUTBOUND_QUERY_PARAM    = var.outbound_tracking_query_param
      DYNAMODB_LIKE_COUNTS_TABLE     = aws_dynamodb_table.like_counts.name
    })
  }
}

resource "aws_iam_role" "evening" {
  name               = "${var.project_name}-evening-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "evening_policy" {
  role = aws_iam_role.evening.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Query"]
        Resource = [aws_dynamodb_table.items.arn, "${aws_dynamodb_table.items.arn}/index/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/pulso-ia/telegram-bot-token"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

data "archive_file" "evening" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/evening_summary"
  output_path = "${path.module}/../dist/evening_summary.zip"
}

resource "aws_lambda_function" "evening" {
  filename         = data.archive_file.evening.output_path
  function_name    = "${var.project_name}-evening-summary"
  role             = aws_iam_role.evening.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  source_code_hash = data.archive_file.evening.output_base64sha256
  layers           = [aws_lambda_layer_version.shared.arn]

  environment {
    variables = merge(local.common_env, {
      TELEGRAM_CHANNEL_ID = var.telegram_channel_id
      TIMEZONE            = "America/Argentina/Buenos_Aires"
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
      Resource = [aws_lambda_function.fetch.arn, aws_lambda_function.filter.arn]
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
        Default = "Done"
      }
      FilterAINews = {
        Type       = "Task"
        Resource   = aws_lambda_function.filter.arn
        InputPath  = "$.fetch_result"
        ResultPath = "$.filter_result"
        Next       = "Done"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          MaxAttempts     = 2
          IntervalSeconds = 30
        }]
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

  schedule_expression = "rate(1 hour)"

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

resource "aws_iam_role" "scheduler_lambdas" {
  name               = "${var.project_name}-scheduler-lambdas"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler_lambdas_policy" {
  role = aws_iam_role.scheduler_lambdas.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [aws_lambda_function.publish.arn, aws_lambda_function.evening.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "publish_ticker" {
  name = "${var.project_name}-publish-ticker"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "rate(15 minutes)"

  target {
    arn      = aws_lambda_function.publish.arn
    role_arn = aws_iam_role.scheduler_lambdas.arn
  }
}

resource "aws_scheduler_schedule" "evening" {
  name = "${var.project_name}-evening-ar"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 21 * * ? *)"
  schedule_expression_timezone = "America/Argentina/Buenos_Aires"

  target {
    arn      = aws_lambda_function.evening.arn
    role_arn = aws_iam_role.scheduler_lambdas.arn
  }
}

resource "aws_lambda_permission" "allow_scheduler_publish" {
  statement_id  = "AllowExecutionFromEventBridgeSchedulerPublish"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.publish.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.publish_ticker.arn
}

resource "aws_lambda_permission" "allow_scheduler_evening" {
  statement_id  = "AllowExecutionFromEventBridgeSchedulerEvening"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.evening.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.evening.arn
}

resource "aws_cloudwatch_log_group" "evening" {
  name              = "/aws/lambda/${aws_lambda_function.evening.function_name}"
  retention_in_days = 7
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "dynamodb_table" {
  value = aws_dynamodb_table.items.name
}

output "dynamodb_engagement_table" {
  value = aws_dynamodb_table.engagement.name
}

# Base de la API HTTP (registrar webhook: POST /webhook/telegram)
output "engagement_api_base" {
  value = aws_apigatewayv2_api.engagement.api_endpoint
}

output "telegram_webhook_url" {
  value = "${trimsuffix(aws_apigatewayv2_api.engagement.api_endpoint, "/")}/webhook/telegram"
}
