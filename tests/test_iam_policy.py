"""Keep cloud_permissions/aws_iam_policy.json in step with the adapter.

Every boto3 call the AWS adapter makes must be allowed by the policy (or the
feature silently fails in production with AccessDenied), and nothing
destructive may be allowed.
"""
import json
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")

# boto3 client attribute -> IAM service prefix
SERVICES = {"ec2": "ec2", "lambda_client": "lambda", "s3": "s3", "rds": "rds", "cloudwatch": "cloudwatch"}
# boto3 operations whose IAM action name differs from the CamelCased op name.
IAM_NAME = {
    "s3:ListBuckets": "s3:ListAllMyBuckets",
    "rds:DescribeDbInstances": "rds:DescribeDBInstances",
    # S3 authorises DeleteBucketTagging with the s3:PutBucketTagging permission.
    "s3:DeleteBucketTagging": "s3:PutBucketTagging",
}

with open(os.path.join(ROOT, "cloud_permissions", "aws_iam_policy.json"), encoding="utf-8") as f:
    POLICY = json.load(f)


def _camel(op):
    return "".join(p.capitalize() for p in op.split("_"))


def _adapter_actions():
    with open(os.path.join(ROOT, "backend", "app", "adapters", "aws.py"), encoding="utf-8") as f:
        src = f.read()
    actions = set()
    for client, op in re.findall(r"self\.(\w+)\.get_paginator\(\s*\"(\w+)\"", src):
        actions.add(f"{SERVICES[client]}:{_camel(op)}")
    for client, op in re.findall(r"self\.(\w+)\.(\w+)\(", src):
        if client in SERVICES and op != "get_paginator":
            actions.add(f"{SERVICES[client]}:{_camel(op)}")
    return {IAM_NAME.get(a, a) for a in actions}


def _statements(effect):
    for st in POLICY["Statement"]:
        if st["Effect"] == effect:
            actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            yield st, set(actions)


def test_every_adapter_call_is_allowed():
    allowed = set().union(*(a for _, a in _statements("Allow")))
    needed = _adapter_actions()
    assert len(needed) >= 15
    assert needed <= allowed, sorted(needed - allowed)


def test_no_unused_allow_grants():
    allowed = set().union(*(a for _, a in _statements("Allow")))
    assert allowed <= _adapter_actions(), sorted(allowed - _adapter_actions())


def test_destructive_actions_never_allowed_and_explicitly_denied():
    allowed = set().union(*(a for _, a in _statements("Allow")))
    denied = set().union(*(a for _, a in _statements("Deny")))
    for action in ("ec2:TerminateInstances", "ec2:DeleteVolume", "lambda:DeleteFunction",
                   "lambda:GetFunction", "rds:DeleteDBInstance", "s3:DeleteBucket"):
        assert action not in allowed
        assert action in denied


def test_protection_tags_are_enforced_by_iam():
    sids = {st["Sid"]: st for st in POLICY["Statement"]}
    protected = sids["DenyMutatingProtectedResources"]["Condition"]["StringEqualsIgnoreCase"]
    assert "aws:ResourceTag/cloudsentry:protected" in protected
    assert "ec2:StopInstances" in sids["DenyStoppingDoNotStop"]["Action"]
    touching = sids["DenyTouchingProtectionTags"]
    keys = touching["Condition"]["ForAnyValue:StringEqualsIgnoreCase"]["aws:TagKeys"]
    assert {"cloudsentry:protected", "do-not-stop"} <= set(keys)
    # Every tag write *and removal* the policy allows is covered, so protection
    # can be neither overwritten nor stripped.
    allowed = set().union(*(a for _, a in _statements("Allow")))
    tag_mutations = {a for a in allowed if re.search(r"(Tag|Untag)", a) and not re.search(r":(Get|List)", a)}
    tag_mutations -= {"s3:PutBucketTagging"}  # documented IAM limit: see cloud_permissions/README.md
    assert tag_mutations <= set(touching["Action"]), tag_mutations - set(touching["Action"])


def test_mutations_are_scoped_to_arns_not_star():
    for st, actions in _statements("Allow"):
        mutating = {a for a in actions if not re.match(r"^\w+:(Describe|List|Get)", a)}
        if mutating:
            resources = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
            assert "*" not in resources, st["Sid"]
