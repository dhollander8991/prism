import * as cdk from "aws-cdk-lib";
import * as ecr from "aws-cdk-lib/aws-ecr";
import { Construct } from "constructs";

/**
 * ECR lives in its OWN stack, deployed BEFORE the app stack, for one reason:
 * the app stack's ECS service references the backend image by tag. If ECR and the
 * service were in the same stack, the first `cdk deploy` would create the service
 * pointing at an image tag that does not exist yet — the tasks could never pull,
 * the service would never reach steady state, and CloudFormation would hang then
 * roll the whole stack back (deleting everything, including the empty repo).
 *
 * Splitting it lets the deploy order be: deploy ECR -> build & push image -> deploy
 * app. By the time the service is created, `:latest` exists and the first task is
 * healthy on the first try. `cdk destroy --all` tears both down in dependency order.
 */
export class PrismEcrStack extends cdk.Stack {
  public readonly repository: ecr.Repository;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.repository = new ecr.Repository(this, "PrismBackendRepo", {
      repositoryName: "prism-backend",
      lifecycleRules: [
        {
          rulePriority: 1,
          description: "Keep last 3 images",
          maxImageCount: 3,
          tagStatus: ecr.TagStatus.ANY,
        },
      ],
      // Tear-down-safe: DESTROY + emptyOnDelete means `cdk destroy` removes the repo
      // even if images remain, so nothing lingers to bill for.
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
    });

    new cdk.CfnOutput(this, "EcrRepoUri", {
      exportName: "PrismEcrUri",
      value: this.repository.repositoryUri,
      description: "ECR repository URI — used by make build-push",
    });
  }
}
