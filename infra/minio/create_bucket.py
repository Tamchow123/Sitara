"""One-shot MinIO bucket initialisation (runs in the api image, so no extra
pinned mc image is needed).

Creates the private media bucket idempotently and ensures NO anonymous
bucket policy exists — generated designs stay private; delivery will use
signed URLs or authenticated streaming in a later phase."""

import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def main() -> int:
    endpoint = os.environ["S3_ENDPOINT_URL"]
    bucket = os.environ["S3_BUCKET_NAME"]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("S3_REGION_NAME", "us-east-1"),
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 5}),
    )

    try:
        client.head_bucket(Bucket=bucket)
        print(f"bucket {bucket!r} already exists")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchBucket"}:
            raise
        client.create_bucket(Bucket=bucket)
        print(f"created bucket {bucket!r}")

    # MinIO buckets are private by default; make that explicit by removing
    # any anonymous-access policy that might exist.
    try:
        client.delete_bucket_policy(Bucket=bucket)
        print(f"removed bucket policy from {bucket!r} (bucket is private)")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchBucketPolicy"}:
            print(f"bucket {bucket!r} has no anonymous policy (private)")
        else:
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
