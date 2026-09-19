output "idle_instance_id" {
  description = "The ID of the idle demo EC2 instance"
  value       = aws_instance.idle_demo.id
}

output "active_instance_id" {
  description = "The ID of the active demo EC2 instance"
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
