terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# 1. Demo EC2 Instance (Idle)
resource "aws_instance" "idle_demo" {
  ami           = data.aws_ami.amazon_linux_2.id
  instance_type = "t2.micro"

  tags = {
    Name        = "CloudSentry-Demo-Idle-EC2"
    Environment = "Demo"
    ManagedBy   = "Terraform"
  }
}

# 2. Demo EC2 Instance (Active)
resource "aws_instance" "active_demo" {
  ami           = data.aws_ami.amazon_linux_2.id
  instance_type = "t3.micro"

  tags = {
    Name        = "CloudSentry-Demo-Active-EC2"
    Environment = "Demo"
    ManagedBy   = "Terraform"
  }
}

# 3. Demo EBS Volume (Unattached/Orphaned)
resource "aws_ebs_volume" "orphaned_volume" {
  availability_zone = "${var.aws_region}a"
  size              = 10

  tags = {
    Name        = "CloudSentry-Demo-Orphaned-EBS"
    Environment = "Demo"
  }
}

# 4. Demo S3 Bucket (For discovery)
resource "aws_s3_bucket" "demo_bucket" {
  bucket_prefix = "cloudsentry-demo-bucket-"
  
  tags = {
    Name        = "CloudSentry-Demo-Bucket"
    Environment = "Demo"
  }
}

# Data source to fetch latest Amazon Linux 2 AMI
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }
}
