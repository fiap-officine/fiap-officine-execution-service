output "execucoes_table" {
  value = aws_dynamodb_table.execucoes.name
}

output "estoque_table" {
  value = aws_dynamodb_table.estoque.name
}

output "commands_queue_url" {
  value = aws_sqs_queue.commands.url
}

output "commands_dlq_arn" {
  description = "DLQ monitorada pelo alerta do New Relic"
  value       = aws_sqs_queue.commands_dlq.arn
}

output "iam_policy_arn" {
  value = aws_iam_policy.execution_service.arn
}
