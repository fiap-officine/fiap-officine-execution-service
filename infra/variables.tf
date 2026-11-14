variable "aws_region" {
  description = "Região AWS"
  type        = string
  default     = "us-east-2"
}

variable "project" {
  description = "Prefixo do projeto"
  type        = string
  default     = "fiap-officine"
}

variable "environment" {
  description = "Ambiente (homolog | production)"
  type        = string

  validation {
    condition     = contains(["homolog", "production"], var.environment)
    error_message = "environment deve ser homolog ou production."
  }
}

variable "max_receive_count" {
  description = "Tentativas antes de mover a mensagem para a DLQ"
  type        = number
  default     = 5
}

variable "node_role_name" {
  description = "Role IAM do nó K3s (output k3s_node_role_name do repo fiap-officine-kubernets). Vazio = não anexar."
  type        = string
  default     = ""
}
