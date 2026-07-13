#!/usr/bin/env bash
# Load the local data-only dump into the deployed RDS instance WITHOUT exposing RDS.
# RDS is not publicly accessible and its SG only admits the Fargate task. So we open an
# SSM port-forwarding session THROUGH the running task (which can reach RDS) and restore
# over that tunnel. Nothing about the persistent security posture changes.
#
# Prereqs: AWS CLI, the `session-manager-plugin`, and a local `psql` client.
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-prism}"
AWS_REGION="${AWS_REGION:-eu-central-1}"
CLUSTER="${ECS_CLUSTER:-prism}"
SERVICE="${ECS_SERVICE:-prism-backend}"
DUMP_FILE="${DUMP_FILE:-prism_data.sql}"
LOCAL_PORT="${LOCAL_PORT:-15432}"
STACK="PrismStack"

aws() { command aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

command -v session-manager-plugin >/dev/null 2>&1 || {
  echo "ERROR: install the AWS session-manager-plugin first."; exit 1; }
command -v psql >/dev/null 2>&1 || { echo "ERROR: psql not found."; exit 1; }
[ -f "$DUMP_FILE" ] || { echo "ERROR: $DUMP_FILE not found — run 'make dump'."; exit 1; }

echo "--- Finding the running task ---"
TASK_ARN=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
  --desired-status RUNNING --query 'taskArns[0]' --output text)
[ "$TASK_ARN" != "None" ] && [ -n "$TASK_ARN" ] || {
  echo "ERROR: no RUNNING task in $SERVICE. Is the service healthy?"; exit 1; }
TASK_ID="${TASK_ARN##*/}"
RUNTIME_ID=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].runtimeId' --output text)

echo "--- Resolving RDS endpoint + credentials ---"
DB_SECRET_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='DbSecretArn'].OutputValue" --output text)
CREDS=$(aws secretsmanager get-secret-value --secret-id "$DB_SECRET_ARN" \
  --query SecretString --output text)
get() { printf '%s' "$CREDS" | python3 -c "import sys,json;print(json.load(sys.stdin)['$1'])"; }
DB_HOST=$(get host); DB_PORT=$(get port); DB_USER=$(get username)
DB_PASS=$(get password); DB_NAME=$(get dbname)

echo "--- Opening SSM tunnel: localhost:$LOCAL_PORT -> $DB_HOST:$DB_PORT (via task $TASK_ID) ---"
aws ssm start-session \
  --target "ecs:${CLUSTER}_${TASK_ID}_${RUNTIME_ID}" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"$DB_HOST\"],\"portNumber\":[\"$DB_PORT\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}" \
  >/dev/null 2>&1 &
SSM_PID=$!
trap 'kill "$SSM_PID" 2>/dev/null || true' EXIT
sleep 8  # give the tunnel time to establish

echo "--- Restoring into RDS (truncate + load; schema already migrated by the task) ---"
export PGPASSWORD="$DB_PASS"
PSQL="psql -h localhost -p $LOCAL_PORT -U $DB_USER -d $DB_NAME -v ON_ERROR_STOP=1"
$PSQL -c "TRUNCATE feedback_items, clusters, insight_reports, theme_trends, pipeline_state;"
$PSQL -f "$DUMP_FILE"

echo "--- Done. Row counts: ---"
$PSQL -c "SELECT 'feedback_items' t, count(*) FROM feedback_items
          UNION ALL SELECT 'clusters', count(*) FROM clusters
          UNION ALL SELECT 'insight_reports', count(*) FROM insight_reports
          UNION ALL SELECT 'theme_trends', count(*) FROM theme_trends;"
echo "Verify the live app:  curl http://<AlbDnsName>/api/v1/insights"
