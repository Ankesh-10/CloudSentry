# Optional: the principal the CloudSentry backend runs as, with exactly the
# policy in cloud_permissions/aws_iam_policy.json (the same file the tests
# check against the adapter). Off by default: most setups manage IAM elsewhere.
#
#   terraform apply -var create_cloudsentry_role=true \
#     -var 'cloudsentry_trusted_principal_arn=arn:aws:iam::<acct>:role/<runner>'
#
# A role (assumed by your runtime) is preferred over an IAM user with static
# keys; see cloud_permissions/README.md.

resource "aws_iam_policy" "cloudsentry" {
  count       = var.create_cloudsentry_role ? 1 : 0
  name_prefix = "cloudsentry-agent-"
  description = "CloudSentry least-privilege policy (cloud_permissions/aws_iam_policy.json)"
  policy      = file("${path.module}/../cloud_permissions/aws_iam_policy.json")
}

resource "aws_iam_role" "cloudsentry" {
  count       = var.create_cloudsentry_role ? 1 : 0
  name_prefix = "cloudsentry-agent-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = var.cloudsentry_trusted_principal_arn }
      Action    = "sts:AssumeRole"
    }]
  })
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "cloudsentry" {
  count      = var.create_cloudsentry_role ? 1 : 0
  role       = aws_iam_role.cloudsentry[0].name
  policy_arn = aws_iam_policy.cloudsentry[0].arn
}
