# Terraform stub

One small real module: a versioned S3 bucket for archiving the static GTFS
zips that `carma-load-gtfs` consumes, with a lifecycle rule expiring
superseded versions. It exists to show the shape of the IaC layer, not a full
cloud footprint; CI runs `terraform fmt -check` and `terraform validate`
(provider schema only — nothing is planned or applied, and no state or
credentials exist in this repo).

```sh
terraform init -backend=false
terraform validate
```
