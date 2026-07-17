# One real module, small on purpose: an S3 bucket for archiving static GTFS
# feed zips (the input to carma-load-gtfs), versioned so a bad upstream
# publish never destroys the previous snapshot. Nothing here is applied by CI
# and no state or credentials live in the repo.

terraform {
  required_version = ">= 1.5"

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

resource "aws_s3_bucket" "gtfs_static_snapshots" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_versioning" "gtfs_static_snapshots" {
  bucket = aws_s3_bucket.gtfs_static_snapshots.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "gtfs_static_snapshots" {
  bucket = aws_s3_bucket.gtfs_static_snapshots.id

  rule {
    id     = "expire-noncurrent-snapshots"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_retention_days
    }
  }
}

resource "aws_s3_bucket_public_access_block" "gtfs_static_snapshots" {
  bucket = aws_s3_bucket.gtfs_static_snapshots.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
