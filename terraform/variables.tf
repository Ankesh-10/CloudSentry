variable "aws_region" {
  description = "The AWS region to deploy the demo environment into (must match the IAM policy's region)"
  type        = string
  default     = "us-east-1"
}

variable "create_cloudsentry_role" {
  description = "Also create the IAM role the CloudSentry backend assumes"
  type        = bool
  default     = false
}

variable "cloudsentry_trusted_principal_arn" {
  description = "ARN allowed to assume the CloudSentry role (required when create_cloudsentry_role = true)"
  type        = string
  default     = ""

  validation {
    condition     = !var.create_cloudsentry_role || can(regex("^arn:aws:iam::[0-9]{12}:", var.cloudsentry_trusted_principal_arn))
    error_message = "Set cloudsentry_trusted_principal_arn to an IAM ARN when create_cloudsentry_role is true."
  }
}
