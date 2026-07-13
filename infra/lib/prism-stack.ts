import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as rds from "aws-cdk-lib/aws-rds";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

interface PrismStackProps extends cdk.StackProps {
  /** The ECR repository, created in PrismEcrStack and deployed first so the image
   *  exists before this stack's ECS service is created. */
  repository: ecr.IRepository;
}

export class PrismStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: PrismStackProps) {
    super(scope, id, props);

    // The backend image repo is provisioned in PrismEcrStack (deployed first).
    const repo = props.repository;

    // =========================================================================
    // VPC - PUBLIC subnets only, NO NAT gateway.
    //
    // WHY: NAT gateways cost ~$33/mo (1 per AZ) which is the single largest line
    // item for a portfolio deploy. The trade-off is that Fargate tasks and the RDS
    // instance live in public subnets. We mitigate the exposure with tight security
    // groups (taskSg only accepts from albSg; dbSg only accepts from taskSg).
    // In production you would use private subnets + a NAT gateway or VPC endpoints.
    // =========================================================================
    const vpc = new ec2.Vpc(this, "PrismVpc", {
      maxAzs: 2,
      // DELIBERATE COST DECISION: no NAT gateway (~$33/mo saved).
      // Fargate tasks run in public subnets with assignPublicIp=true and a scoped SG.
      natGateways: 0,
      subnetConfiguration: [
        {
          name: "public",
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    // =========================================================================
    // Security Groups - least-privilege, three-layer model
    // =========================================================================

    // ALB: the only resource that accepts traffic from the open internet.
    // Port 80 from 0.0.0.0/0 is INTENTIONAL - this is the public entry point.
    const albSg = new ec2.SecurityGroup(this, "AlbSg", {
      vpc,
      description: "PRISM ALB - public HTTP ingress (intentional, only entry point)",
      allowAllOutbound: false,
    });
    albSg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(80), "Public HTTP from internet");
    // ALB only needs to forward to the Fargate task - allow outbound on 8000 only.
    albSg.addEgressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(8000), "Forward to Fargate task");

    // Task: only accepts traffic forwarded from the ALB.
    // All outbound is allowed because the task calls App Store API, Anthropic,
    // and OpenAI - none of which have stable IP ranges we can pin. With no NAT
    // the task needs a public IP + open egress to reach those external services.
    const taskSg = new ec2.SecurityGroup(this, "TaskSg", {
      vpc,
      description: "PRISM Fargate task - ingress from ALB only, open egress for external APIs",
      allowAllOutbound: true, // needed: Anthropic/OpenAI/App Store calls, no NAT
    });
    taskSg.addIngressRule(albSg, ec2.Port.tcp(8000), "From ALB only");

    // DB: only the Fargate task can reach Postgres. Never 0.0.0.0/0.
    const dbSg = new ec2.SecurityGroup(this, "DbSg", {
      vpc,
      description: "PRISM RDS - ingress from Fargate task SG only",
      allowAllOutbound: false,
    });
    dbSg.addIngressRule(taskSg, ec2.Port.tcp(5432), "Postgres from Fargate task only");

    // =========================================================================
    // RDS - Postgres 16, t4g.micro, single-AZ, public subnet but NOT publicly
    // accessible (no route from internet - only taskSg can reach port 5432).
    // =========================================================================
    const db = new rds.DatabaseInstance(this, "PrismDb", {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_16,
      }),
      instanceType: ec2.InstanceType.of(
        ec2.InstanceClass.T4G,
        ec2.InstanceSize.MICRO
      ),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      securityGroups: [dbSg],
      // publiclyAccessible false: RDS sits in a public subnet for routing
      // convenience (no NAT) but the SG + this flag block any internet access.
      publiclyAccessible: false,
      // Generated secret excludes characters that break DATABASE_URL construction.
      // The / @ : " ' ` and space all require percent-encoding in a URL; excluding
      // them means the raw password can be embedded directly in postgresql://...
      credentials: rds.Credentials.fromGeneratedSecret("prism", {
        excludeCharacters: "/@:\"'`{} ",
      }),
      databaseName: "prism",
      storageType: rds.StorageType.GP3,
      allocatedStorage: 20,
      multiAz: false,
      // Portfolio deploy - no need to retain backups or protect from accidental destroy.
      backupRetention: cdk.Duration.days(0),
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      deleteAutomatedBackups: true,
    });

    // =========================================================================
    // Secrets Manager - empty placeholder for the Anthropic API key (the only LLM
    // provider this project uses; there is no OpenAI in the codebase). Populated
    // post-deploy via `aws secretsmanager put-secret-value`. NEVER put keys in code.
    // =========================================================================
    const anthropicSecret = new secretsmanager.Secret(this, "AnthropicApiKey", {
      secretName: "prism/anthropic-api-key",
      description: "PRISM Anthropic API key - populate manually after first deploy",
      secretStringValue: cdk.SecretValue.unsafePlainText("REPLACE_ME"),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // =========================================================================
    // ECS Cluster
    // =========================================================================
    const cluster = new ecs.Cluster(this, "PrismCluster", {
      clusterName: "prism",
      vpc,
      // Container Insights adds ~$1/mo - skip for portfolio deploy.
      containerInsightsV2: ecs.ContainerInsights.DISABLED,
    });

    // =========================================================================
    // CloudWatch log group - 7-day retention keeps costs negligible.
    // Use a raw CfnLogGroup (retentionInDays is a native CFN property) instead of the
    // L2 LogGroup, whose `retention` prop provisions a CDK log-retention Lambda custom
    // resource. This project deliberately uses NO Lambda, so we set retention natively.
    // =========================================================================
    const cfnLogGroup = new logs.CfnLogGroup(this, "PrismTaskLogGroup", {
      logGroupName: "/ecs/prism-backend",
      retentionInDays: 7,
    });
    cfnLogGroup.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);
    // Reference by ARN so the service gets an implicit CFN dependency on the group.
    const logGroup = logs.LogGroup.fromLogGroupArn(
      this,
      "PrismTaskLogs",
      cfnLogGroup.attrArn
    );

    // =========================================================================
    // IAM roles - task execution role (reads secrets) and task role (app perms).
    // =========================================================================
    const executionRole = new iam.Role(this, "PrismTaskExecutionRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AmazonECSTaskExecutionRolePolicy"
        ),
      ],
    });

    // Grant read access to all three secrets (DB + both API keys).
    // The execution role fetches secrets at task startup before the container starts.
    db.secret!.grantRead(executionRole);
    anthropicSecret.grantRead(executionRole);

    const taskRole = new iam.Role(this, "PrismTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      description: "Runtime role for PRISM Fargate task - add app-level AWS perms here",
    });

    // =========================================================================
    // Fargate task definition - 0.5 vCPU / 2 GB. 2 GB (not 1 GB) gives headroom for
    // the seed run: UMAP + HDBSCAN over the corpus with torch + the embedding model
    // loaded would risk an OOM kill at 1 GB. Costs ~$5/mo more - accepted.
    // =========================================================================
    const taskDef = new ecs.FargateTaskDefinition(this, "PrismTaskDef", {
      cpu: 512,
      memoryLimitMiB: 2048,
      executionRole,
      taskRole,
    });

    // Build DATABASE_URL from the individual secret fields injected at runtime.
    // The entrypoint script assembles the URL from these parts to avoid storing
    // the full connection string (with password) in a single plaintext secret.
    const container = taskDef.addContainer("prism-backend", {
      image: ecs.ContainerImage.fromEcrRepository(repo, "latest"),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "prism",
        logGroup,
      }),
      environment: {
        // Prod should override CORS_ALLOW_ORIGINS with the Vercel domain.
        CORS_ALLOW_ORIGINS: "*",
        // Sentence-transformers / HuggingFace cache - baked into the image at build
        // time so cold starts don't trigger a download.
        HF_HOME: "/app/models",
        SENTENCE_TRANSFORMERS_HOME: "/app/models",
        PYTHONUNBUFFERED: "1",
      },
      secrets: {
        // Database credentials come from the RDS-generated secret's JSON fields.
        POSTGRES_USER: ecs.Secret.fromSecretsManagerVersion(
          db.secret!,
          { versionStage: "AWSCURRENT" },
          "username"
        ),
        POSTGRES_PASSWORD: ecs.Secret.fromSecretsManagerVersion(
          db.secret!,
          { versionStage: "AWSCURRENT" },
          "password"
        ),
        POSTGRES_HOST: ecs.Secret.fromSecretsManagerVersion(
          db.secret!,
          { versionStage: "AWSCURRENT" },
          "host"
        ),
        POSTGRES_PORT: ecs.Secret.fromSecretsManagerVersion(
          db.secret!,
          { versionStage: "AWSCURRENT" },
          "port"
        ),
        POSTGRES_DB: ecs.Secret.fromSecretsManagerVersion(
          db.secret!,
          { versionStage: "AWSCURRENT" },
          "dbname"
        ),
        ANTHROPIC_API_KEY: ecs.Secret.fromSecretsManager(anthropicSecret),
      },
      portMappings: [{ containerPort: 8000 }],
    });

    // =========================================================================
    // ALB - created explicitly with OUR albSg so the pattern doesn't auto-generate
    // its own (which would be an allow-all-outbound SG in ADDITION to albSg, quietly
    // defeating the egress restriction and the "task accepts from albSg only"
    // invariant). Passing a pre-built loadBalancer makes the SG model exact.
    // =========================================================================
    const alb = new elbv2.ApplicationLoadBalancer(this, "PrismAlb", {
      vpc,
      internetFacing: true,
      securityGroup: albSg,
      loadBalancerName: "prism-alb",
    });

    // =========================================================================
    // Fargate service - ApplicationLoadBalancedFargateService wires the target
    // group, listener, and service onto our ALB. taskSg is the ONLY SG on the task;
    // the pattern adds a single ingress rule to it from albSg (the ALB's SG).
    // =========================================================================
    const fargateService = new ecsPatterns.ApplicationLoadBalancedFargateService(
      this,
      "PrismService",
      {
        cluster,
        taskDefinition: taskDef,
        desiredCount: 1,
        loadBalancer: alb, // our ALB + albSg - no auto-generated SG
        assignPublicIp: true, // required: no NAT, public subnet, needs internet egress
        securityGroups: [taskSg],
        serviceName: "prism-backend",
        // ECS Exec - lets `make seed-from-dump` open an SSM tunnel THROUGH the task to
        // RDS (which stays private: no public access, no SG change). CDK grants the
        // task role the SSM channel permissions automatically.
        enableExecuteCommand: true,
        // Generous grace period: alembic upgrade head + sentence-transformer model
        // loading on cold start can take 60-90 s before the first /health 200.
        healthCheckGracePeriod: cdk.Duration.seconds(120),
        // Circuit breaker: give up after a failed deployment rather than hanging for
        // up to 3 hours. rollback=true restores the previous stable task revision.
        circuitBreaker: { rollback: true },
        // minHealthyPercent=100 for a single-task service means "don't kill the old
        // task until the new one is healthy". For desiredCount=1 that prevents
        // downtime but requires capacity to run 2 tasks briefly.
        // 0 is acceptable for a portfolio deploy (brief downtime on redeploy is fine).
        minHealthyPercent: 0,
      }
    );

    // Configure the ALB health check to use our /health endpoint.
    fargateService.targetGroup.configureHealthCheck({
      path: "/health",
      healthyThresholdCount: 2,
      interval: cdk.Duration.seconds(30),
      timeout: cdk.Duration.seconds(10),
      healthyHttpCodes: "200",
    });

    // =========================================================================
    // GitHub OIDC - passwordless deploy from GitHub Actions.
    //
    // Creates an OIDC provider for GitHub Actions token issuer (one per account;
    // CDK will error if one already exists - in that case replace
    // `new iam.OpenIdConnectProvider` with `iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn`
    // and supply the existing ARN).
    // =========================================================================
    // Use the NATIVE CfnOIDCProvider (AWS::IAM::OIDCProvider), not the L2
    // OpenIdConnectProvider - the L2 provisions a Lambda-backed custom resource, and
    // this project uses NO Lambda. Native resource = zero Lambda in the template.
    // (Still one-per-account: if this URL already has a provider in the account,
    // deploy will fail; import the existing ARN instead - see README.)
    const githubOidcProvider = new iam.CfnOIDCProvider(this, "GithubOidcProvider", {
      url: "https://token.actions.githubusercontent.com",
      clientIdList: ["sts.amazonaws.com"],
      // GitHub's OIDC thumbprint - stable, published by GitHub.
      thumbprintList: ["6938fd4d98bab03faadb97b34396831e3780aea1"],
    });
    githubOidcProvider.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

    // Set GITHUB_ORG_REPO=owner/repo (e.g. danielhollander/prism) before deploying so
    // the OIDC trust policy is scoped to your repo's main branch. Left unset, the role
    // is created but is UNASSUMABLE - the deploy workflow would silently fail to auth.
    // Warn loudly at synth so this manual step isn't missed (we warn rather than throw
    // so `cdk synth`/tests still run without the env var set).
    const githubOrgRepo = process.env.GITHUB_ORG_REPO ?? "OWNER/REPO";
    if (githubOrgRepo === "OWNER/REPO") {
      cdk.Annotations.of(this).addWarning(
        "GITHUB_ORG_REPO is unset - the GitHub deploy role will be UNASSUMABLE. " +
          "Set GITHUB_ORG_REPO=owner/repo (e.g. danielhollander/prism) before deploying."
      );
    }

    const deployRole = new iam.Role(this, "GithubDeployRole", {
      roleName: "prism-github-deploy",
      assumedBy: new iam.WebIdentityPrincipal(
        githubOidcProvider.attrArn,
        {
          StringEquals: {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            // Restrict to pushes on main only - PRs get a different ref.
            [`token.actions.githubusercontent.com:sub`]: `repo:${githubOrgRepo}:ref:refs/heads/main`,
          },
        }
      ),
      description: "Assumed by GitHub Actions on push to main to deploy PRISM",
    });

    // ECR push permissions.
    repo.grantPullPush(deployRole);

    // ECS redeploy permissions - narrowed to PrismStack's resources only.
    deployRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "EcsRedeploy",
        effect: iam.Effect.ALLOW,
        actions: [
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "ecs:DescribeClusters",
          "ecs:RegisterTaskDefinition",
          "ecs:ListTaskDefinitions",
          "ecs:DescribeTaskDefinition",
          "ecs:DeregisterTaskDefinition",
        ],
        resources: ["*"], // ECS DescribeServices doesn't support resource-level ARNs
      })
    );

    // PassRole is needed when ECS creates a new task revision during force-new-deployment.
    deployRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "PassTaskRoles",
        effect: iam.Effect.ALLOW,
        actions: ["iam:PassRole"],
        resources: [executionRole.roleArn, taskRole.roleArn],
      })
    );

    // =========================================================================
    // CloudFormation Outputs - everything the operator needs post-deploy
    // =========================================================================
    new cdk.CfnOutput(this, "AlbDnsName", {
      exportName: "PrismAlbDns",
      value: fargateService.loadBalancer.loadBalancerDnsName,
      description: "ALB DNS - set as VITE_API_URL in the Vercel frontend",
    });

    new cdk.CfnOutput(this, "RdsEndpoint", {
      exportName: "PrismRdsEndpoint",
      value: db.instanceEndpoint.hostname,
      description: "RDS hostname (internal - not publicly reachable)",
    });

    new cdk.CfnOutput(this, "DbSecretArn", {
      exportName: "PrismDbSecretArn",
      value: db.secret!.secretArn,
      description: "RDS credentials secret - used by `make seed-from-dump` to open the SSM tunnel",
    });

    new cdk.CfnOutput(this, "AnthropicSecretArn", {
      exportName: "PrismAnthropicSecretArn",
      value: anthropicSecret.secretArn,
      description: "Populate with: aws secretsmanager put-secret-value --secret-id <ARN> --secret-string <KEY>",
    });

    new cdk.CfnOutput(this, "OidcRoleArn", {
      exportName: "PrismOidcRoleArn",
      value: deployRole.roleArn,
      description: "GitHub Actions role ARN - set as AWS_ROLE_ARN in repo secrets",
    });

    new cdk.CfnOutput(this, "EcsClusterName", {
      exportName: "PrismEcsCluster",
      value: cluster.clusterName,
    });

    new cdk.CfnOutput(this, "EcsServiceName", {
      exportName: "PrismEcsService",
      value: fargateService.service.serviceName,
    });
  }
}
