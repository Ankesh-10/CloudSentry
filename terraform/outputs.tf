output "idle_instance_id" {
  description = "The ID of the idle demo EC2 instance"
  value       = aws_instance.idle_demo.id
}

output "protected_instance_id" {
  description = "The ID of the protected demo EC2 instance (must never be stopped)"
  value       = aws_instance.active_demo.id
}

output "orphaned_volume_id" {
  description = "The ID of the unattached EBS volume"
  value       = aws_ebs_volume.orphaned_volume.id
}

output "demo_bucket_name" {
  description = "The name of the demo S3 bucket"
  value       = aws_s3_bucket.demo_bucket.id
}

output "demo_lambda_name" {
  description = "The demo Lambda function"
  value       = aws_lambda_function.demo.function_name
}

output "demo_lambda_role_arn" {
  description = "Execution role of the demo Lambda (DEMO_LAMBDA_ROLE_ARN for scripts/provision_demo.sh)"
  value       = aws_iam_role.demo_lambda.arn
}

output "cloudsentry_role_arn" {
  description = "Role for the CloudSentry backend (when create_cloudsentry_role = true)"
  value       = var.create_cloudsentry_role ? aws_iam_role.cloudsentry[0].arn : null
}
