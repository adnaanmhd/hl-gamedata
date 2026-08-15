#!/usr/bin/env bash
# Provision the Phase-1 pipeline VM + backup bucket (plan §7.2, R19/F10/F12).
#
# Idempotent: safe to re-run; each resource is created only if absent.
# Creates:
#   - VM `hl-pipeline-vm`: e2-standard-16, on-demand, asia-south1-a with
#     -b/-c fallback on capacity errors, Debian 12, 250 GB pd-balanced,
#     NO service account, NO scopes (F10: the VM itself holds no Google
#     identity — Drive/GCS access is via the pipeline-runner SA key file),
#     no HTTP/S ingress (SSH via `gcloud compute ssh` / IAP only, F12).
#   - GCS bucket gs://hl-gamedata-pipeline-backups (asia-south1, Standard,
#     uniform bucket-level access, private), suffixed if the name is taken.
#   - roles/storage.objectAdmin for pipeline-runner@… on THAT BUCKET ONLY.
set -euo pipefail

PROJECT="hl-gamedata-pipeline"
VM="hl-pipeline-vm"
REGION="asia-south1"
ZONES=("asia-south1-a" "asia-south1-b" "asia-south1-c")
MACHINE="e2-standard-16"
DISK_GB=250
BUCKET_BASE="hl-gamedata-pipeline-backups"
SA="pipeline-runner@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT" --quiet >/dev/null

# --- quota sanity (§7.1): e2 draws from the regional CPUS metric ----------
CPUS=$(gcloud compute regions describe "$REGION" --format=json |
  python3 -c "import json,sys; d=json.load(sys.stdin); \
print(next(int(q['limit']) for q in d['quotas'] if q['metric']=='CPUS'))")
if [ "$CPUS" -lt 16 ]; then
  echo "FATAL: $REGION CPUS quota is $CPUS (<16) — file a bump before provisioning" >&2
  exit 1
fi
echo "quota ok: $REGION CPUS limit=$CPUS"

# --- VM (zone fallback on capacity errors) --------------------------------
if gcloud compute instances describe "$VM" --zone="${ZONES[0]}" >/dev/null 2>&1 ||
   gcloud compute instances describe "$VM" --zone="${ZONES[1]}" >/dev/null 2>&1 ||
   gcloud compute instances describe "$VM" --zone="${ZONES[2]}" >/dev/null 2>&1; then
  echo "VM $VM already exists — skipping create"
else
  created=""
  for Z in "${ZONES[@]}"; do
    echo "creating $VM in $Z ..."
    if gcloud compute instances create "$VM" \
        --zone="$Z" \
        --machine-type="$MACHINE" \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size="${DISK_GB}GB" --boot-disk-type=pd-balanced \
        --no-service-account --no-scopes \
        --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring; then
      created="$Z"
      break
    fi
    echo "zone $Z failed (likely capacity) — trying next" >&2
  done
  [ -n "$created" ] || { echo "FATAL: all three zones failed" >&2; exit 1; }
  echo "VM created in $created"
fi

# --- backup bucket (suffix if the global name is taken by someone else) ----
BUCKET=""
for CAND in "$BUCKET_BASE" "${BUCKET_BASE}-hl" "${BUCKET_BASE}-$(date +%s)"; do
  if gcloud storage buckets describe "gs://$CAND" >/dev/null 2>&1; then
    # exists — ours (same project) or someone else's?
    if gcloud storage buckets describe "gs://$CAND" --format="value(name)" 2>/dev/null | grep -q .; then
      BUCKET="$CAND"; echo "bucket gs://$CAND already ours — reusing"; break
    fi
    continue
  fi
  if gcloud storage buckets create "gs://$CAND" \
      --location="$REGION" --default-storage-class=STANDARD \
      --uniform-bucket-level-access --public-access-prevention 2>/dev/null; then
    BUCKET="$CAND"; echo "bucket gs://$CAND created"; break
  fi
  echo "bucket name $CAND unavailable — trying suffix" >&2
done
[ -n "$BUCKET" ] || { echo "FATAL: could not create a backup bucket" >&2; exit 1; }

# --- bucket-scoped grant for the pipeline SA (F10: this bucket ONLY) ------
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role="roles/storage.objectAdmin" >/dev/null
echo "granted roles/storage.objectAdmin on gs://$BUCKET to $SA"

# --- acceptance (§18.9) ----------------------------------------------------
ZONE=$(gcloud compute instances list --filter="name=$VM" --format="value(zone)")
echo "acceptance: VM zone=$ZONE"
gcloud compute ssh "$VM" --zone="$ZONE" --command=true --quiet && echo "acceptance: SSH ok"
gcloud storage ls "gs://$BUCKET" >/dev/null && echo "acceptance: bucket listable"
gcloud storage buckets get-iam-policy "gs://$BUCKET" \
  --format=json | grep -q "$SA" && echo "acceptance: IAM grant visible"
echo "DONE: VM=$VM zone=$ZONE bucket=gs://$BUCKET"
