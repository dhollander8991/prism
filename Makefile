# PRISM deploy Makefile
# ---------------------------------------------------------------------------
# Prerequisites: AWS CLI configured with profile "prism", Docker, CDK 2.x.
#
# DEPLOY ORDER (first time):
#   1. make deploy          # ECR stack -> build & push image -> app stack, in order.
#                           # Because the image exists before the ECS service is
#                           # created, the first task is healthy on the first try.
#   2. Populate the two API-key secrets (see README / put-secret-value)
#   3. make redeploy        # force a new task so ECS picks up the populated secrets
#   4. make seed            # once /health returns 200, load initial data
# ---------------------------------------------------------------------------

AWS_PROFILE  := prism
AWS_REGION   := eu-central-1
AWS_ACCOUNT  := 186048966015
ECR_REPO     := $(AWS_ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com/prism-backend
ECS_CLUSTER  := prism
ECS_SERVICE  := prism-backend
IMAGE_TAG    := $(shell git rev-parse --short HEAD 2>/dev/null || echo latest)

# Local dev DB (source of the real corpus) and the dump file it writes.
LOCAL_DB_URL ?= postgresql://prism:prism@localhost:5433/prism
DUMP_FILE    ?= prism_data.sql

# ALB DNS from CloudFormation output (override with: make seed ALB_URL=http://...)
ALB_URL      ?= $(shell aws cloudformation describe-stacks \
                  --stack-name PrismStack \
                  --region $(AWS_REGION) \
                  --profile $(AWS_PROFILE) \
                  --query "Stacks[0].Outputs[?OutputKey=='AlbDnsName'].OutputValue" \
                  --output text 2>/dev/null | awk '{print "http://" $$1}')

.PHONY: ecr-login build-push deploy deploy-ecr deploy-app destroy seed dump seed-from-dump redeploy help

help:
	@echo ""
	@echo "  make deploy          — full ordered first deploy: ECR -> image -> app stack"
	@echo "  make build-push      — build image + push to ECR (latest + git SHA)"
	@echo "  make redeploy        — force ECS to pull a new task (after image/secrets change)"
	@echo "  make seed            — fetch ~200 fresh App Store reviews + run the pipeline"
	@echo "  make dump            — pg_dump the local corpus (data-only) to $(DUMP_FILE)"
	@echo "  make seed-from-dump  — load $(DUMP_FILE) into RDS via an SSM tunnel (real 1,820 corpus)"
	@echo "  make destroy         — tear down BOTH stacks (app + ECR), all resources"
	@echo "  make ecr-login       — authenticate Docker to ECR"
	@echo ""

ecr-login:
	aws ecr get-login-password \
	  --region $(AWS_REGION) \
	  --profile $(AWS_PROFILE) \
	  | docker login \
	      --username AWS \
	      --password-stdin $(AWS_ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com

build-push: ecr-login
	@echo "--- Building Docker image ---"
	docker build -t prism-backend backend/
	@echo "--- Tagging: latest + $(IMAGE_TAG) ---"
	docker tag prism-backend:latest $(ECR_REPO):latest
	docker tag prism-backend:latest $(ECR_REPO):$(IMAGE_TAG)
	@echo "--- Pushing to ECR ---"
	docker push $(ECR_REPO):latest
	docker push $(ECR_REPO):$(IMAGE_TAG)

# NOTE: cdk deploy requires the CDK bootstrap stack to exist in the account.
# Run `cdk bootstrap aws://186048966015/eu-central-1 --profile prism` once if
# you see "This stack uses assets..." errors.

# Full first-time deploy, correctly ordered: the ECR repo must exist and hold the
# image BEFORE the app stack's ECS service is created, or the service can't pull.
deploy: deploy-ecr build-push deploy-app

deploy-ecr:
	cd infra && cdk deploy PrismEcrStack --profile $(AWS_PROFILE) --require-approval never

deploy-app:
	cd infra && cdk deploy PrismStack --profile $(AWS_PROFILE) --require-approval never

destroy:
	@echo "WARNING: This deletes BOTH stacks (app + ECR) including RDS data and images."
	@echo "Press Ctrl-C within 5 seconds to abort..."
	@sleep 5
	cd infra && cdk destroy --all --profile $(AWS_PROFILE) --force

# Force ECS to pull a fresh task revision. Use after:
#  - pushing a new image to ECR
#  - populating/rotating secrets in Secrets Manager
redeploy:
	aws ecs update-service \
	  --cluster $(ECS_CLUSTER) \
	  --service $(ECS_SERVICE) \
	  --force-new-deployment \
	  --region $(AWS_REGION) \
	  --profile $(AWS_PROFILE) \
	  --output text \
	  --query "service.serviceName"

# Requires ALB_URL to be set (auto-resolved from CFN outputs if not overridden).
# The pipeline/run call is synchronous — it may take 30-60 s for a full run.
seed:
	@if [ -z "$(ALB_URL)" ]; then \
	  echo "ERROR: Could not resolve ALB_URL. Is PrismStack deployed?"; \
	  echo "Override with: make seed ALB_URL=http://<your-alb-dns>"; \
	  exit 1; \
	fi
	@echo "--- Seeding: $(ALB_URL) ---"
	@echo "Step 1: sync App Store (Notion) reviews..."
	curl -sf -X POST "$(ALB_URL)/connectors/app-store/sync" \
	  -H "Content-Type: application/json" \
	  -d '{"app_id": "1465002394", "country": "us", "limit": 200}' \
	  | python3 -m json.tool
	@echo ""
	@echo "Step 2: run the pipeline..."
	curl -sf -X POST "$(ALB_URL)/pipeline/run" \
	  -H "Content-Type: application/json" \
	  | python3 -m json.tool

# Data-only dump of the local corpus (schema is created on RDS by `alembic upgrade head`,
# so we only move rows). Excludes alembic_version. Then scan the dump for anything that
# looks like a secret — the corpus is public review data and stores no keys, but confirm.
dump:
	@echo "--- pg_dump (data-only) from $(LOCAL_DB_URL) ---"
	pg_dump "$(LOCAL_DB_URL)" --data-only --no-owner --no-privileges \
	  --exclude-table=alembic_version --file=$(DUMP_FILE)
	@echo "--- Scanning $(DUMP_FILE) for secret-like strings (API keys / AWS creds) ---"
	@if grep -aoE 'sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|aws_secret_access_key' $(DUMP_FILE); then \
	  echo "ABORT: secret-like string found in the dump (shown above)."; exit 1; \
	fi
	@echo "OK: no secrets found. $(DUMP_FILE) = $$(wc -l < $(DUMP_FILE)) lines, $$(du -h $(DUMP_FILE) | cut -f1)."

# Restore the local corpus into RDS. RDS stays PRIVATE — we tunnel through the running
# Fargate task via SSM (no public access, no SG change). Requires the AWS
# session-manager-plugin and a local psql client.
seed-from-dump:
	@test -f $(DUMP_FILE) || { echo "No $(DUMP_FILE) — run 'make dump' first."; exit 1; }
	AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION) ECS_CLUSTER=$(ECS_CLUSTER) \
	  ECS_SERVICE=$(ECS_SERVICE) DUMP_FILE=$(DUMP_FILE) \
	  bash scripts/seed_from_dump.sh
