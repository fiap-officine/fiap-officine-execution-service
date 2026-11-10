# ──────────────────────────────────────────────
# Infra própria do Execution Service
#   - DynamoDB: tabelas `execucoes` (single-table + GSI fila-por-status + TTL)
#     e `estoque` — banco NoSQL exclusivo do serviço
#   - Fila execution-commands (+ DLQ)
#   - Política IAM: DynamoDB das próprias tabelas, consumir a própria fila e
#     publicar em os-saga-replies
# ──────────────────────────────────────────────
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # terraform init -backend-config="key=execution-service/<env>/terraform.tfstate"
  # backend "s3" {
  #   bucket  = "fiap-officine-terraform-state"
  #   region  = "sa-east-1"
  #   encrypt = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      Service     = "execution-service"
      ManagedBy   = "terraform"
      Repository  = "fiap-officine-execution-service"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  prefix      = "${var.project}-${var.environment}"
  replies_arn = "arn:aws:sqs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.prefix}-os-saga-replies"
}

# ── DynamoDB (on-demand: sem custo ocioso) ──────
resource "aws_dynamodb_table" "execucoes" {
  name         = "${local.prefix}-execucoes"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  attribute {
    name = "GSI1PK"
    type = "S"
  }
  attribute {
    name = "GSI1SK"
    type = "S"
  }

  # Fila ordenada por status/prioridade e índice esparso do outbox
  global_secondary_index {
    name            = "fila-por-status"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  # Itens MSG# (idempotência) expiram após MSG_TTL_DAYS
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.environment == "production"
  }
}

resource "aws_dynamodb_table" "estoque" {
  name         = "${local.prefix}-estoque"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"

  attribute {
    name = "PK"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.environment == "production"
  }
}

# ── SQS ─────────────────────────────────────────
resource "aws_sqs_queue" "commands_dlq" {
  name                      = "${local.prefix}-execution-commands-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "commands" {
  name                       = "${local.prefix}-execution-commands"
  visibility_timeout_seconds = 30
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.commands_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "commands_dlq" {
  queue_url = aws_sqs_queue.commands_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.commands.arn]
  })
}

# ── IAM ─────────────────────────────────────────
data "aws_iam_policy_document" "execution_service" {
  statement {
    sid = "OwnDynamoTables"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:ConditionCheckItem",
      "dynamodb:DescribeTable",
    ]
    resources = [
      aws_dynamodb_table.execucoes.arn,
      "${aws_dynamodb_table.execucoes.arn}/index/*",
      aws_dynamodb_table.estoque.arn,
    ]
  }

  statement {
    sid = "ConsumeOwnQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [aws_sqs_queue.commands.arn]
  }

  statement {
    sid       = "PublishSagaReplies"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl"]
    resources = [local.replies_arn]
  }
}

resource "aws_iam_policy" "execution_service" {
  name        = "${local.prefix}-execution-service"
  description = "Execution Service: DynamoDB próprio, execution-commands e os-saga-replies"
  policy      = data.aws_iam_policy_document.execution_service.json
}

resource "aws_iam_role_policy_attachment" "node" {
  count      = var.node_role_name == "" ? 0 : 1
  role       = var.node_role_name
  policy_arn = aws_iam_policy.execution_service.arn
}
