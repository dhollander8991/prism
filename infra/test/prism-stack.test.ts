/**
 * CDK assertions test for PrismEcrStack + PrismStack.
 *
 * Instantiates the app the same way bin/prism.ts does so that both stacks
 * synthesize and cross-stack references resolve correctly.  Tests assert
 * security-critical properties that a code reviewer would check manually.
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { PrismEcrStack } from "../lib/prism-ecr-stack";
import { PrismStack } from "../lib/prism-stack";

// ---------------------------------------------------------------------------
// Helpers — build both stacks once per test file (synthesis is expensive)
// ---------------------------------------------------------------------------

const env = { account: "123456789012", region: "eu-central-1" };

function buildStacks(): { ecrTemplate: Template; appTemplate: Template } {
  const app = new cdk.App();

  const ecrStack = new PrismEcrStack(app, "PrismEcrStack", { env });
  const appStack = new PrismStack(app, "PrismStack", {
    env,
    repository: ecrStack.repository,
  });

  return {
    ecrTemplate: Template.fromStack(ecrStack),
    appTemplate: Template.fromStack(appStack),
  };
}

// Build once and reuse — avoids re-synthesizing for every test.
const { ecrTemplate, appTemplate } = buildStacks();

// ---------------------------------------------------------------------------
// VPC
// ---------------------------------------------------------------------------

describe("VPC", () => {
  test("has ZERO NAT gateways (cost-critical: no $33/mo line item)", () => {
    // No AWS::EC2::NatGateway resources should exist at all.
    appTemplate.resourceCountIs("AWS::EC2::NatGateway", 0);
  });
});

// ---------------------------------------------------------------------------
// RDS
// ---------------------------------------------------------------------------

describe("RDS", () => {
  test("is NOT publicly accessible", () => {
    appTemplate.hasResourceProperties("AWS::RDS::DBInstance", {
      PubliclyAccessible: false,
    });
  });

  test("is single-AZ", () => {
    appTemplate.hasResourceProperties("AWS::RDS::DBInstance", {
      MultiAZ: false,
    });
  });

  test("has zero backup retention days (portfolio deploy: no backup costs)", () => {
    appTemplate.hasResourceProperties("AWS::RDS::DBInstance", {
      BackupRetentionPeriod: 0,
    });
  });

  test("uses Postgres engine", () => {
    appTemplate.hasResourceProperties("AWS::RDS::DBInstance", {
      Engine: "postgres",
    });
  });

  test("has deletion protection disabled (portfolio: cdk destroy must work)", () => {
    appTemplate.hasResourceProperties("AWS::RDS::DBInstance", {
      DeletionProtection: false,
    });
  });

  test("removal policy is DESTROY — DeletionPolicy Delete on the resource", () => {
    // CDK translates RemovalPolicy.DESTROY into DeletionPolicy: "Delete" on the
    // CloudFormation resource.  hasResource checks the resource-level metadata,
    // not just Properties.
    appTemplate.hasResource("AWS::RDS::DBInstance", {
      DeletionPolicy: "Delete",
    });
  });
});

// ---------------------------------------------------------------------------
// Security Group ingress rules
// ---------------------------------------------------------------------------

describe("Security Groups", () => {
  test("no SG ingress rule opens port 5432 to 0.0.0.0/0", () => {
    const templateJson = appTemplate.toJSON();

    // Collect every AWS::EC2::SecurityGroupIngress standalone resource.
    const standaloneIngress = Object.values(
      templateJson.Resources as Record<string, { Type: string; Properties: Record<string, unknown> }>
    ).filter((r) => r.Type === "AWS::EC2::SecurityGroupIngress");

    const offending5432StandaloneRules = standaloneIngress.filter((r) => {
      const p = r.Properties;
      return (
        (Number(p.FromPort) === 5432 || Number(p.ToPort) === 5432) &&
        p.CidrIp === "0.0.0.0/0"
      );
    });

    expect(offending5432StandaloneRules).toHaveLength(0);

    // Also inspect inline ingress in AWS::EC2::SecurityGroup SecurityGroupIngress arrays.
    const sgResources = Object.values(
      templateJson.Resources as Record<string, { Type: string; Properties: Record<string, unknown> }>
    ).filter((r) => r.Type === "AWS::EC2::SecurityGroup");

    for (const sg of sgResources) {
      const inlineRules = (sg.Properties.SecurityGroupIngress ?? []) as Array<Record<string, unknown>>;
      for (const rule of inlineRules) {
        if (
          (Number(rule.FromPort) === 5432 || Number(rule.ToPort) === 5432) &&
          rule.CidrIp === "0.0.0.0/0"
        ) {
          throw new Error(
            "Found a SecurityGroup inline ingress rule opening port 5432 to 0.0.0.0/0"
          );
        }
      }
    }
  });

  test("ALB security group allows port 80 from 0.0.0.0/0 (public entry point — intentional)", () => {
    // The ALB SG intentionally opens port 80 from the internet.
    // Use the standalone ingress resource that CDK emits for addIngressRule().
    const templateJson = appTemplate.toJSON();

    const resources = Object.values(
      templateJson.Resources as Record<string, { Type: string; Properties: Record<string, unknown> }>
    );

    // Check standalone AWS::EC2::SecurityGroupIngress resources first.
    const standaloneMatch = resources.some((r) => {
      if (r.Type !== "AWS::EC2::SecurityGroupIngress") return false;
      const p = r.Properties;
      return Number(p.FromPort) === 80 && p.CidrIp === "0.0.0.0/0";
    });

    // Also check inline SecurityGroupIngress arrays inside SecurityGroup resources.
    const inlineMatch = resources.some((r) => {
      if (r.Type !== "AWS::EC2::SecurityGroup") return false;
      const inlineRules = (r.Properties.SecurityGroupIngress ?? []) as Array<Record<string, unknown>>;
      return inlineRules.some(
        (rule) => Number(rule.FromPort) === 80 && rule.CidrIp === "0.0.0.0/0"
      );
    });

    expect(standaloneMatch || inlineMatch).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// CloudWatch log group
// ---------------------------------------------------------------------------

describe("CloudWatch log group", () => {
  test("retention is 7 days (ONE_WEEK)", () => {
    appTemplate.hasResourceProperties("AWS::Logs::LogGroup", {
      RetentionInDays: 7,
    });
  });
});

// ---------------------------------------------------------------------------
// Secrets Manager
// ---------------------------------------------------------------------------

describe("Secrets Manager", () => {
  test("prism/anthropic-api-key secret exists", () => {
    appTemplate.hasResourceProperties("AWS::SecretsManager::Secret", {
      Name: "prism/anthropic-api-key",
    });
  });

  test("NO openai secret exists (project uses no OpenAI)", () => {
    const secrets = appTemplate.findResources("AWS::SecretsManager::Secret");
    const names = Object.values(secrets).map(
      (r: any) => r.Properties?.Name
    );
    expect(names).not.toContain("prism/openai-api-key");
  });

  test("synthesized template contains NO real LLM API key material", () => {
    // JSON.stringify the full template so we search every field, including
    // SecretStringValue and any environment variables baked into the task def.
    const templateStr = JSON.stringify(appTemplate.toJSON());
    const ecrStr = JSON.stringify(ecrTemplate.toJSON());
    const combined = templateStr + ecrStr;

    const forbiddenPrefixes = [
      "sk-ant-",   // Anthropic v1 key prefix
      "sk-proj-",  // OpenAI project key prefix
      // Bare "sk-" covers the old OpenAI format; exclude "sk-ant-" and "sk-proj-"
      // to avoid double-reporting, but also catch the plain sk- pattern:
    ];

    for (const prefix of forbiddenPrefixes) {
      expect(combined).not.toContain(prefix);
    }

    // Separately check for bare "sk-" that is NOT part of the forbidden prefixes
    // already checked (to avoid false positives from the prefix strings themselves).
    // We search for "sk-" that is followed by non-"a" and non-"p" characters,
    // i.e., not "sk-ant-" or "sk-proj-", indicating a raw key.
    // Simplest: assert the placeholder "REPLACE_ME" is present and no key looks real.
    // A real Anthropic key looks like "sk-ant-api03-..." (>40 chars after prefix).
    // A real OpenAI key looks like "sk-proj-..." or "sk-<48 alphanum chars>".
    // We check that no token matching /sk-[A-Za-z0-9]{20,}/ appears.
    const realKeyPattern = /sk-[A-Za-z0-9_\-]{20,}/g;
    const matches = combined.match(realKeyPattern) ?? [];

    // Filter out any matches that are obviously the placeholder string itself
    // (REPLACE_ME is only 10 chars after sk-, so it won't match {20,} — but
    // be explicit anyway).
    const realMatches = matches.filter((m) => !m.includes("REPLACE_ME"));

    expect(realMatches).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// ECR stack separation
// ---------------------------------------------------------------------------

describe("ECR stack separation", () => {
  test("PrismStack has ZERO AWS::ECR::Repository resources", () => {
    appTemplate.resourceCountIs("AWS::ECR::Repository", 0);
  });

  test("PrismEcrStack has exactly 1 AWS::ECR::Repository", () => {
    ecrTemplate.resourceCountIs("AWS::ECR::Repository", 1);
  });

  test("ECR repo lifecycle policy limits to 3 images", () => {
    // CDK inlines the lifecycle policy inside the AWS::ECR::Repository resource as
    // LifecyclePolicy.LifecyclePolicyText (a JSON string).  There is no separate
    // AWS::ECR::LifecyclePolicy resource type.
    const templateJson = ecrTemplate.toJSON();
    const repos = Object.values(
      templateJson.Resources as Record<string, { Type: string; Properties: Record<string, unknown> }>
    ).filter((r) => r.Type === "AWS::ECR::Repository");

    expect(repos.length).toBeGreaterThanOrEqual(1);

    const policyFound = repos.some((r) => {
      const lp = r.Properties.LifecyclePolicy as { LifecyclePolicyText?: string } | undefined;
      if (!lp?.LifecyclePolicyText) return false;
      const parsed = JSON.parse(lp.LifecyclePolicyText) as {
        rules: Array<{ selection: { countNumber?: number } }>;
      };
      return parsed.rules.some((rule) => rule.selection?.countNumber === 3);
    });

    expect(policyFound).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// ECS task definition
// ---------------------------------------------------------------------------

describe("ECS Fargate task definition", () => {
  test("is 512 CPU units", () => {
    appTemplate.hasResourceProperties("AWS::ECS::TaskDefinition", {
      Cpu: "512",
    });
  });

  test("is 2048 MB memory (headroom for the seed pipeline)", () => {
    appTemplate.hasResourceProperties("AWS::ECS::TaskDefinition", {
      Memory: "2048",
    });
  });
});
