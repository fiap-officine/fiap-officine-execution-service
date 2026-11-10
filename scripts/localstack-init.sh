#!/bin/bash
# Cria as filas da saga (com DLQ) no LocalStack.
# Executado automaticamente pelo LocalStack (montado em /etc/localstack/init/ready.d).
set -euo pipefail

PREFIX="${SQS_QUEUE_PREFIX:-fiap-officine}"
REGION="${AWS_DEFAULT_REGION:-sa-east-1}"

for QUEUE in billing-commands execution-commands os-saga-replies; do
  DLQ_URL=$(awslocal sqs create-queue --region "$REGION" \
    --queue-name "${PREFIX}-${QUEUE}-dlq" --query QueueUrl --output text)
  DLQ_ARN=$(awslocal sqs get-queue-attributes --region "$REGION" \
    --queue-url "$DLQ_URL" --attribute-names QueueArn \
    --query Attributes.QueueArn --output text)
  awslocal sqs create-queue --region "$REGION" \
    --queue-name "${PREFIX}-${QUEUE}" \
    --attributes "{\"VisibilityTimeout\":\"30\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${DLQ_ARN}\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}" \
    >/dev/null
  echo "Fila ${PREFIX}-${QUEUE} criada (DLQ: ${PREFIX}-${QUEUE}-dlq)"
done
