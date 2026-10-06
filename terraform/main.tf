terraform {
  required_version = ">= 1.5"
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

  # State is local by default (fine for a throwaway demo account). For anything
  # shared, use a remote backend, e.g.:
  # backend "s3" {
  #   bucket         = "<your-tf-state-bucket>"
  #   key            = "cloudsentry/demo.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "<your-lock-table>"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "cloudsentry-demo"
      ManagedBy = "Terraform"
    }
  }
}

# Latest Amazon Linux 2023 (AL2 is end-of-life); same source as provision_demo.sh.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# 1. Demo EC2 instance that sits idle -> idle_compute anomaly -> stop_ec2.
resource "aws_instance" "idle_demo" {
  ami           = data.aws_ssm_parameter.al2023.value
  instance_type = "t3.micro"

  tags = {
    Name        = "CloudSentry-Demo-Idle-EC2"
    Environment = "Demo"
    # No Owner tag on purpose: also demonstrates the untagged_resource rule.
  }
}

# 2. Demo EC2 instance that is protected: must never be stopped by CloudSentry.
resource "aws_instance" "active_demo" {
  ami           = data.aws_ssm_parameter.al2023.value
  instance_type = "t3.micro"

  tags = {
    Name                    = "CloudSentry-Demo-Protected-EC2"
    Environment             = "Demo"
    Owner                   = "demo"
    "cloudsentry:protected" = "true"
  }
}

# 3. Demo EBS volume, unattached -> unused_volume anomaly -> recommend_review.
resource "aws_ebs_volume" "orphaned_volume" {
  availability_zone = "${var.aws_region}a"
  size              = 1
  type              = "gp3"

  tags = {
    Name        = "CloudSentry-Demo-Orphaned-EBS"
    Environment = "Demo"
    Owner       = "demo"
  }
}

# 4. Demo S3 bucket (discovery and tagging).
resource "aws_s3_bucket" "demo_bucket" {
  bucket_prefix = "cloudsentry-demo-bucket-"
  force_destroy = true

  tags = {
    Name        = "CloudSentry-Demo-Bucket"
    Environment = "Demo"
  }
}

resource "aws_s3_bucket_public_access_block" "demo_bucket" {
  bucket                  = aws_s3_bucket.demo_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 5. Demo Lambda (same function provision_demo.sh creates) and its role.
resource "aws_iam_role" "demo_lambda" {
  name_prefix = "cloudsentry-demo-lambda-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "demo_lambda_logs" {
  role       = aws_iam_role.demo_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "archive_file" "demo_lambda" {
  type        = "zip"
  output_path = "${path.module}/.build/demo_lambda.zip"
  source {
    content  = "def handler(event, context):\n    return \"Hello\"\n"
    filename = "dummy_lambda.py"
  }
}

resource "aws_lambda_function" "demo" {
  function_name    = "CloudSentry-Demo-Lambda"
  role             = aws_iam_role.demo_lambda.arn
  runtime          = "python3.12"
  handler          = "dummy_lambda.handler"
  filename         = data.archive_file.demo_lambda.output_path
  source_code_hash = data.archive_file.demo_lambda.output_base64sha256

  tags = {
    Environment = "Demo"
  }
}
