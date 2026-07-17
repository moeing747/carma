output "bucket_name" {
  description = "Name of the GTFS static snapshot bucket."
  value       = aws_s3_bucket.gtfs_static_snapshots.bucket
}

output "bucket_arn" {
  description = "ARN of the GTFS static snapshot bucket."
  value       = aws_s3_bucket.gtfs_static_snapshots.arn
}
