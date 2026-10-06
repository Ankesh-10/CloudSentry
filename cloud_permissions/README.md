# Cloud permissions

`aws_iam_policy.json` is the least-privilege policy for the CloudSentry backend's
AWS principal. `tests/test_iam_policy.py` keeps it in step with the adapter:
every boto3 call the adapter makes is allowed, nothing else is, and destructive
actions are explicitly denied.

## Before you attach it

- **Region.** The policy is written for `us-east-1`. If `AWS_DEFAULT_REGION` is
  different, replace every `us-east-1` in the file. With `AWS_REGIONS` (several
  regions), list them all: `"aws:RequestedRegion": ["us-east-1", "eu-west-1"]`
  and one ARN per region in each `Remediate*`/`TagInRegion` statement (or
  `arn:aws:ec2:*:*:instance/*` if every region is acceptable).
- **Account.** ARNs use `*` for the account id, which only ever matches the
  account the credentials belong to. You may pin it to your account id.

## What the policy enforces (independently of the app)

The app's safety layer already refuses these; the policy makes them hold even
with leaked credentials or an app bug:

| Guard | Statement |
|---|---|
| No stop/start/concurrency change/tagging on resources tagged `cloudsentry:protected=true` | `DenyMutatingProtectedResources` |
| No stopping instances tagged `do-not-stop=true` | `DenyStoppingDoNotStop` |
| Cannot add, overwrite or remove the `cloudsentry:protected` / `do-not-stop` tag keys (so protection cannot be stripped) | `DenyTouchingProtectionTags` |
| No delete/terminate/modify, no reading function code or objects, no IAM | `DenyDestructiveAlways` |

Tag *removal* (`ec2:DeleteTags`, `lambda:UntagResource`,
`rds:RemoveTagsFromResource`) is allowed only so an `apply_tags` action can be
rolled back; the app removes only the keys it added, and only while they still
hold the value it wrote.
| Writes only to instance, volume, function and DB ARNs in the configured region | `Remediate*`, `TagInRegion` |

## Known limits of IAM here

- **S3 bucket tagging replaces the whole tag set.** IAM cannot stop a
  `PutBucketTagging` call from dropping an existing tag. The app always merges
  with the current tags before writing; protect critical buckets with an S3
  bucket policy that denies `s3:PutBucketTagging` to this principal.
- **Lambda reserved concurrency value.** IAM cannot restrict the number passed
  to `PutFunctionConcurrency`; the app refuses values `<= 0` (which would
  disable a function). Protected functions are covered by the tag Deny.
- **Read-only discovery calls** (`Describe*`, `List*`) do not support
  resource-level scoping in AWS, so they use `"Resource": "*"` restricted by
  `aws:RequestedRegion`. S3 listing is global and cannot be region-restricted.

## Credentials

Prefer short-lived credentials over static access keys. The adapter uses
boto3's default credential chain, so with `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` **unset** it picks up, in order:

1. **An IAM role** on the host (EC2 instance profile, ECS task role, EKS IRSA).
2. **OIDC / web identity federation** — set `AWS_ROLE_ARN` and
   `AWS_WEB_IDENTITY_TOKEN_FILE` (e.g. GitHub Actions, or a platform that
   issues OIDC tokens).
3. **Static keys** (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) — only when
   the platform offers neither (e.g. Render). Then:
   - create a dedicated IAM user with only this policy attached, no console
     access, and nothing else;
   - rotate the key at least every 90 days (create the new key, deploy it,
     confirm discovery succeeds, then deactivate and delete the old one);
   - store it only in the platform's secret store, never in the repo.

## GCP

`gcp_custom_role.yaml` is the matching custom role for the (incomplete) GCP
adapter: list/get/start/stop instances, set labels, and read Cloud Monitoring.
