terraform {
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
        Default = "Done"
      }
      FilterAINews = {
        Type       = "Task"
        Resource   = aws_lambda_function.filter.arn
        InputPath  = "$.fetch_result"
        ResultPath = "$.filter_result"
        Next       = "HasRelevantItems"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          MaxAttempts     = 2
          IntervalSeconds = 30
        }]
      }
      HasRelevantItems = {
        Type = "Choice"
        Choices = [{
          Variable           = "$.filter_result.total_relevant"
          NumericGreaterThan = 0
          Next               = "PublishTelegram"
        }]
        Default = "Done"
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

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "dynamodb_table" {
  value = aws_dynamodb_table.items.name
}
