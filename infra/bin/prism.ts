#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { PrismEcrStack } from "../lib/prism-ecr-stack";
import { PrismStack } from "../lib/prism-stack";

const app = new cdk.App();

// Pin to the portfolio AWS account and region so `cdk deploy --profile prism` from
// any machine always lands in the right place. This also enables AZ lookups.
const env = { account: "186048966015", region: "eu-central-1" };

// Deploy ECR FIRST, then build & push the image, then the app stack — see
// PrismEcrStack's comment for why. `cdk deploy --all` respects this dependency order,
// but on the FIRST deploy you must push the image between the two (see the Makefile).
const ecrStack = new PrismEcrStack(app, "PrismEcrStack", {
  env,
  description: "PRISM — ECR repository for the backend image (deploy before PrismStack)",
});

new PrismStack(app, "PrismStack", {
  env,
  repository: ecrStack.repository,
  description:
    "PRISM — product-feedback intelligence platform (portfolio deploy, tear-down-safe)",
});
