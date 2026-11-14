# Contratos de mensageria (v1)

Todas as mensagens usam o [envelope padrão](envelope.schema.json). Os três
serviços mantêm uma cópia idêntica desta pasta.

## Filas (Amazon SQS, standard, long polling 20s)

Nome físico: `fiap-officine-<ambiente>-<fila>`. Cada fila tem uma DLQ
(`<fila>-dlq`) com `maxReceiveCount = 5`.

| Fila | Produtor | Consumidor | Mensagens |
|---|---|---|---|
| `billing-commands` | OS Service | Billing | `GerarOrcamento`, `SolicitarPagamento`, `EstornarPagamento`, `CancelarOrcamento` |
| `execution-commands` | OS Service | Execution | `ReservarEEnfileirar`, `LiberarReserva` |
| `os-saga-replies` | Billing, Execution | OS Service | todas as respostas e eventos abaixo |

## Payloads

| Tipo | Kind | De → Para | Payload |
|---|---|---|---|
| `GerarOrcamento` | command | OS → Billing | `{cliente:{id,nome,email}, veiculo:{id,placa}, itens:[{tipo:"servico"\|"peca", ref_id, descricao, quantidade, valor_unitario}]}` |
| `OrcamentoGerado` | reply | Billing → OS | `{orcamento_id, valor_total, expira_em}` |
| `OrcamentoFalhou` | reply | Billing → OS | `{motivo}` |
| `OrcamentoAprovado` | event | Billing → OS | `{orcamento_id}` |
| `OrcamentoRecusado` | event | Billing → OS | `{orcamento_id, motivo}` |
| `SolicitarPagamento` | command | OS → Billing | `{orcamento_id, valor, descricao}` |
| `PagamentoSolicitado` | reply | Billing → OS | `{pagamento_id, checkout_url}` |
| `PagamentoConfirmado` | event | Billing → OS | `{pagamento_id, mp_payment_id, valor, metodo}` |
| `PagamentoRecusado` | event | Billing → OS | `{pagamento_id, status_detail}` |
| `EstornarPagamento` | command (compensação) | OS → Billing | `{orcamento_id, pagamento_id, motivo}` |
| `PagamentoEstornado` | reply | Billing → OS | `{pagamento_id, mp_refund_id, estorno_necessario}` |
| `CancelarOrcamento` | command (compensação) | OS → Billing | `{orcamento_id, motivo}` |
| `OrcamentoCancelado` | reply | Billing → OS | `{orcamento_id}` |
| `ReservarEEnfileirar` | command | OS → Execution | `{pecas:[{peca_id, quantidade}], servicos:[{servico_id, quantidade, tempo_estimado_minutos}], prioridade}` |
| `ExecucaoAgendada` | reply | Execution → OS | `{posicao_fila}` |
| `ReservaFalhou` | reply | Execution → OS | `{faltantes:[{peca_id, disponivel, necessario}]}` |
| `LiberarReserva` | command (compensação) | OS → Execution | `{motivo}` |
| `ReservaLiberada` | reply | Execution → OS | `{liberada}` |
| `DiagnosticoIniciado` | event | Execution → OS | `{mecanico, observacao, timestamp}` |
| `ReparoIniciado` | event | Execution → OS | `{mecanico, observacao, timestamp}` (**pivô**) |
| `ExecucaoFinalizada` | event | Execution → OS | `{mecanico, observacao, timestamp}` |

Valores monetários trafegam como string decimal (`"221.00"`).

## Garantias

- **At-least-once + idempotência**: todo consumidor registra `message_id`
  (Postgres: `processed_messages`; DynamoDB: item `MSG#<id>` condicional).
- **Outbox**: produtores gravam a mensagem na mesma transação da mudança de
  estado (Postgres: tabela `outbox`; DynamoDB: item `OUTBOX#` no mesmo
  `TransactWriteItems`) e um relay publica no SQS.
- **Ordem**: filas standard não garantem ordem; o orquestrador tolera eventos
  de progresso fora de ordem e ignora respostas inesperadas para o estado.
- **Compatibilidade**: campos novos são opcionais; mudança incompatível exige
  `version: 2`.
