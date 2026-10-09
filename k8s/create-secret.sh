#!/bin/sh
set -eu

[ "${1:-}" = prd ] || { echo "usage: sh k8s/create-secret.sh prd [env-file]" >&2; exit 2; }
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
file=${2:-"$root/.env.prd"}
[ -f "$file" ] || { echo "Missing .env.prd; see .env.prd.example" >&2; exit 1; }

umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM

extract_keys() {
    awk -v keys="$1" '
        BEGIN { count = split(keys, order, " "); for (index_key = 1; index_key <= count; index_key++) wanted[order[index_key]] = 1 }
        {
            sub(/\r$/, "")
            separator = index($0, "=")
            if (!separator) next
            key = substr($0, 1, separator - 1)
            if (!(key in wanted)) next
            if (key in values) { print "Duplicate secret key: " key > "/dev/stderr"; failed = 1 }
            values[key] = substr($0, separator + 1)
        }
        END {
            for (index_key = 1; index_key <= count; index_key++) {
                key = order[index_key]
                if (values[key] ~ /^[[:space:]]*$/ || values[key] ~ /REPLACE-ME/) {
                    print "Missing secret value: " key > "/dev/stderr"
                    failed = 1
                }
            }
            if (failed) exit 1
            for (index_key = 1; index_key <= count; index_key++) {
                key = order[index_key]
                print key "=" values[key]
            }
        }
    ' "$file"
}

extract_keys "RADAR_DB_URL" > "$work/srvapp.env"
extract_keys "RADAR_DB_URL MARKET_INSIGHT_DATABASE_URL AI_PRIMARY_API_KEY AI_SYNTH_API_KEY FMP_API_KEY" > "$work/worker.env"

for component in srvapp worker; do
    kubectl -n ih create secret generic "ih-prd-remote-opinion-radar-$component-secret" \
        --from-env-file="$work/$component.env" --dry-run=client -o yaml > "$work/$component.yaml"
    kubectl label --local -f "$work/$component.yaml" \
        app=ih-prd-remote-opinion-radar "component=$component" env=prd -o yaml > "$work/$component-labeled.yaml"
done

kubectl apply --server-side --field-manager=ih-prd-remote-opinion-radar-secrets \
    -f "$work/srvapp-labeled.yaml" -f "$work/worker-labeled.yaml"
echo "PRD Secrets updated. Restart ih-prd-remote-opinion-radar-srvapp and ih-prd-remote-opinion-radar-worker to load new values."