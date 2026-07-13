# PRISM Infrastructure

AWS CDK (TypeScript) stack that deploys the PRISM backend to ECS Fargate + RDS Postgres + ALB on a budget tight enough for a portfolio project.

---

## Architecture

```
Internet
   │
   ▼  port 80
 ALB (albSg — 0.0.0.0/0 ingress, intentional)
   │
   ▼  port 8000 (taskSg)
 ECS Fargate task   ──► Anthropic / App Store (public internet egress)
   │
   ▼  port 5432 (dbSg)
 RDS Postgres 16 + pgvector
```

All resources live in **one public VPC, no NAT gateway**. The ALB is the only public entry point. The Fargate task and RDS sit in public subnets but are protected by tightly-scoped security groups.

### Why no NAT gateway?

NAT gateways cost ~$32-33/month per AZ ($0.045/hr × 730 hr). For a 2-AZ setup that's ~$65/mo — the single largest line item. A portfolio deploy doesn't need NAT because:

- The Fargate task has `assignPublicIp: true` and a public subnet route to the internet gateway, so it can reach Anthropic/App Store directly.
- The RDS instance is in a public subnet too, but `publiclyAccessible: false` + the `dbSg` (ingress from `taskSg` only) mean it is not reachable from the internet.
- **In production** you would add private subnets, at least one NAT gateway, and move both the task and RDS into private subnets.

---

## Estimated Monthly Cost (eu-central-1, on-demand, as of mid-2026)

| Service | SKU | Estimated cost/mo |
|---------|-----|-------------------|
| RDS PostgreSQL | db.t4g.micro, 20 GB gp3, single-AZ | ~$15 |
| ALB | 1 ALB × 730 hr + LCU | ~$18-20 |
| ECS Fargate | 0.5 vCPU / **2 GB**, 730 hr | ~$22 |
| Public IPv4 | Fargate task IP (+ ALB IPs) × $0.005/hr | ~$4-11 |
| Secrets Manager | 2 secrets × $0.40 (DB + Anthropic) | ~$0.80 |
| CloudWatch Logs | 7-day retention, low volume | ~$0.50 |
| ECR storage | ~1 GB × 3 images | ~$0.30 |
| Data transfer | outbound to internet (light) | ~$1-2 |
| **Total** | | **~$62-72/mo** |

> NAT gateway (NOT used here) would add ~$33-65/mo on top of this.
> At ~$65/mo this burns a $24 credit in ~11 days — **destroy promptly**, or
> `aws ecs update-service --desired-count 0` between demos to pause Fargate spend.

---

## Step-by-Step Deploy (First Time)

### Prerequisites

- AWS CLI configured: `aws configure --profile prism` (account 186048966015, eu-central-1)
- Docker 20+
- CDK 2.x: `npm install -g aws-cdk`
- Node 18+

### 1. Set your GitHub repo for the OIDC deploy role

The OIDC trust policy is scoped to `repo:<owner>/<repo>:ref:refs/heads/main`. Export your
repo before deploying (used at synth time):

```bash
export GITHUB_ORG_REPO=danielhollander/prism   # your GitHub owner/repo
```

If unset, `cdk synth`/`deploy` prints a loud warning and the deploy role is created but
**unassumable** (CI can't authenticate). It's an env var, not a code edit.

### 2. Bootstrap CDK (once per account/region)

```bash
cdk bootstrap aws://186048966015/eu-central-1 --profile prism
```

### 3. Deploy — one ordered command

```bash
make deploy
```

This is deliberately **ordered across two stacks** so the first deploy is healthy on the
first try (no unhealthy-service dance):

1. `cdk deploy PrismEcrStack` — creates just the ECR repo.
2. `make build-push` — builds and pushes the backend image to that repo.
3. `cdk deploy PrismStack` — VPC, SGs, RDS, ECS cluster (0.5 vCPU / **2 GB**), ALB, Fargate
   service, OIDC role, and the single empty Anthropic API-key secret. The service references
   the image that already exists, so the first task pulls and passes `/health` immediately.

The first image build takes 10-20 minutes (compiling C extensions + the CPU-only PyTorch
wheel); later builds hit Docker layer cache (~2-3 min). Final image size ≈ **2.74 GB**.

### 4. Populate the Anthropic API-key secret

The secret is created empty (`REPLACE_ME`). Populate it before the pipeline's Claude calls
work. (There is no OpenAI secret — this project uses no OpenAI.)

```bash
ARN=$(aws cloudformation describe-stacks --stack-name PrismStack --profile prism \
  --query "Stacks[0].Outputs[?OutputKey=='AnthropicSecretArn'].OutputValue" --output text)

aws secretsmanager put-secret-value --secret-id "$ARN" \
  --secret-string "sk-ant-..." --profile prism
```

**Important:** the ECS task reads secrets at startup, so force a new deployment afterward:

```bash
make redeploy
```

Wait for the service to stabilise (`aws ecs describe-services` or the console).

### 5. Verify health

```bash
ALB=$(aws cloudformation describe-stacks \
  --stack-name PrismStack \
  --profile prism \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDnsName'].OutputValue" \
  --output text)

curl http://$ALB/health
# {"status":"ok","service":"prism"}
```

### 6. Seed data

Two options:

**A. Fresh reviews (quick).** Fetch ~200 recent Notion App Store reviews and run the full
pipeline on the live instance:

```bash
make seed
```

**B. The real 1,820-review corpus (recommended).** Load your local dev DB into RDS so the
live app has the full historical corpus (which the Alerter needs for its trend history):

```bash
make dump            # pg_dump the local corpus (data-only) -> prism_data.sql
                     # also scans the dump and confirms it contains no API keys / creds
make seed-from-dump  # load it into RDS
```

`seed-from-dump` keeps RDS **private**: it opens an SSM port-forward *through the running
Fargate task* (which is the only thing allowed to reach RDS) and restores over that tunnel —
no public access, no security-group change. Prereqs: the AWS `session-manager-plugin` and a
local `psql` client. The dump is data-only; the schema is already created on RDS by the
task's `alembic upgrade head`, and the load truncates the tables first so it's idempotent.

---

## GitHub Actions Continuous Deploy

The workflow in `.github/workflows/deploy.yml` runs on every push to `main`:

1. Runs the backend pytest suite (with a Postgres service container).
2. Assumes the CDK-created OIDC role (no stored AWS keys).
3. Builds and pushes the image to ECR.
4. Calls `aws ecs update-service --force-new-deployment`.

### Required GitHub repo secrets (Settings → Secrets and variables → Actions)

| Secret | Value |
|--------|-------|
| `AWS_ROLE_ARN` | The `OidcRoleArn` output from `cdk deploy` |
| `ECR_REPO` | e.g. `186048966015.dkr.ecr.eu-central-1.amazonaws.com/prism-backend` |
| `ECS_CLUSTER` | `prism` |
| `ECS_SERVICE` | `prism-backend` |

---

## Connecting the Vercel Frontend

The frontend is a TanStack (Vite) app — deploy it to Vercel (free), not to AWS.

1. From `frontend/`: `vercel deploy` (or connect the repo in the Vercel dashboard).
2. Set `VITE_API_URL=http://<AlbDnsName>` in the Vercel project env, then redeploy the frontend.
3. Scope the backend CORS to your Vercel domain: set `CORS_ALLOW_ORIGINS` on the ECS service
   to e.g. `https://prism.vercel.app` (the CDK default is `*`), then `make redeploy`.
   Note: the backend only sends `Access-Control-Allow-Credentials: true` once
   `CORS_ALLOW_ORIGINS` is a specific origin — with the `*` default, credentials are disabled
   (a wildcard + credentials is unsafe and non-compliant).

---

## HOW TO DESTROY EVERYTHING

> **This is irreversible. All RDS data will be deleted.**

```bash
make destroy
```

This runs `cdk destroy --all` — tearing down **both** stacks in dependency order (the app
stack first, then the ECR stack). The Makefile gives a 5-second abort window, then destroys
without further prompts. Everything is configured to leave nothing billing:
- `removalPolicy: DESTROY` on RDS, ECR, Secrets, and the log group.
- `emptyOnDelete: true` on the ECR repo (so destroy succeeds even if images remain).
- `deletionProtection: false`, `backupRetention: 0`, `deleteAutomatedBackups: true` on RDS
  (no final snapshot, no lingering automated backups).
- No NAT gateways or Elastic IPs exist to leak.

After `cdk destroy` completes, verify in the AWS console:
- **RDS**: no `prism` instance in eu-central-1. Also check for automated snapshots — RDS occasionally creates a final snapshot even with `deleteAutomatedBackups: true`. Delete any manually.
- **ECR**: the `prism-backend` repo should be gone.
- **Secrets Manager**: `prism/anthropic-api-key` deleted. Secrets have a 7-day scheduled deletion by default — if you redeploy within 7 days CDK will fail because the name is taken. Either wait 7 days or use `--force-delete-without-recovery` via the CLI.
- **CloudWatch Logs**: the `/ecs/prism-backend` log group is removed with `DESTROY` policy.
- **ECR images**: if `emptyOnDelete` didn't fire (rare), delete remaining images manually before running `cdk destroy` again.

---

## Notes and Known Limitations

- **Empty secrets before `make seed`**: if `ANTHROPIC_API_KEY` still contains `REPLACE_ME`, the synthesiser will fail with auth errors. Populate them and `make redeploy` first.
- **Image ordering is handled**: `make deploy` deploys ECR → pushes the image → deploys the
  app stack, so the service is healthy on the first task. (This is why ECR is a separate
  stack — if it lived with the service, the first deploy would reference a nonexistent image
  and hang.)
- **`GITHUB_ORG_REPO` for the deploy role**: export it before deploying (see step 1).
  Unset → the OIDC role is unassumable and a loud synth warning fires.
- **OIDC provider conflicts**: this account can hold only one OIDC provider per issuer URL.
  If `token.actions.githubusercontent.com` already exists (e.g. from the InsureCRM stacks),
  `cdk deploy` errors on the duplicate. Remove the `CfnOIDCProvider` from `lib/prism-stack.ts`
  and pass the existing ARN string directly to `WebIdentityPrincipal(...)` instead.
- **No Lambda**: the template contains zero `AWS::Lambda::Function` — log retention uses a
  native `CfnLogGroup`, and the OIDC provider is a native `CfnOIDCProvider` (the L2 versions
  of both provision Lambda-backed custom resources, which this project deliberately avoids).
- **CDK bootstrap**: the first `cdk deploy` requires the CDK toolkit stack. Run
  `cdk bootstrap aws://186048966015/eu-central-1 --profile prism` once if you see asset errors.
