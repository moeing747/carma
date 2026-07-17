variable "aws_region" {
  description = "Region for the snapshot bucket."
  type        = string
  default     = "eu-central-1"
}

variable "bucket_name" {
  description = "Globally unique name for the GTFS static snapshot bucket."
  type        = string
}

variable "noncurrent_version_retention_days" {
  description = "Days to keep superseded snapshot versions before expiry."
  type        = number
  default     = 90
}
