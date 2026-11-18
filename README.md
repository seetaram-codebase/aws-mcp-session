# Building an AWS-Native MCP Server

Talk materials, demo code, and the live workshop kit for the AWS meetup
session "Building an AWS-Native MCP Server: From Concept to Production".
See `session-outline.html` (or `.txt`) for the full run-of-show, corrected
facts, and sources.

## Layout

| Path | What it is |
|---|---|
| `server/` | Speaker's own reference demo -- a Personal Finance Guide MCP server (FastMCP), OAuth 2.1-protected via AWS Cognito. Built live on stage in the "Build: FastMCP server" segment; also what's deployed to ECS (see `infra/terraform/ecs.tf`). |
| `kickstart/` | What attendees clone for the live hands-on segment. Generic OAuth-protected FastMCP template + README -- add your own tool, connect your IDE, done. Also has `submit.py` (step 7): sends your tool to `launcher/` to go live on its own dedicated ECS server. |
| `launcher/` | The "MCP Builder" -- an always-on FastMCP server whose one tool, `submit_server`, takes an attendee's validated tool code and provisions a brand new ECS task + CloudFront domain for it via boto3 (no Terraform/GitHub Actions per attendee). `attendee_base/` is the one shared Docker image every attendee server reuses. |
| `builder-web/` | Click-to-deploy front end for `launcher/` -- describe a tool or paste code, click Deploy, get a live MCP server back. No terminal needed; browser-based OAuth login against the Launcher via Dynamic Client Registration. |
| `electron-client/` | A standalone desktop chat agent -- Bedrock Claude reasoning + the MCP server's tools over the same OAuth flow, no IDE involved. The "MCP isn't just an IDE feature" demo -- can also call `submit_server` directly from chat, with an approval step before anything deploys. |
| `finance-chat-web/` | Browser version of the same chat-agent idea, scoped to the finance server -- Bedrock Claude + `server/finance_mcp_server.py`'s tools, OAuth via Dynamic Client Registration, no desktop app or IDE needed. |
| `local-stdio-demo/` | A stdio-transport MCP server (no network, no OAuth) -- backs the "two shapes of MCP server" and "you don't have to build every server" slides. Wired into `.mcp.json` alongside `uvx mcp-server-fetch` as the local-launch counterpart to everything else in this repo, which is all remote/HTTP. |
| `infra/` | Provisions the single, shared Cognito User Pool (OAuth Authorization Server) plus, now, the ECS Fargate + ALB + CloudFront deployment of `server/`. Cognito: a one-shot `boto3` script, or Terraform (what CI uses). ECS: Terraform only. |
| `.github/workflows/cognito-infra.yml` | Manual (`workflow_dispatch`) pipeline for the Cognito pool: plan/apply/destroy, OIDC to AWS, no stored access keys. |
| `.github/workflows/deploy-mcp-ecs.yml` | Manual pipeline that builds `server/`'s Docker image, pushes to ECR, and applies the same Terraform state with the new image tag -- rolls the ECS service. |
| `session-outline.html` / `.txt`, `session-cfp.txt`, `talk.txt.txt` | The talk content itself: abstract, agenda, corrected facts, sources. |
| `presentation.html` | The actual slide deck -- self-contained, keyboard-navigable (arrows, `N` for speaker notes, `F` for fullscreen). |

## Running the Cognito infra

One shared pool backs both `server/` and `kickstart/` -- see
`infra/terraform/main.tf` for why that's one pool instead of two.

**Via the GitHub Actions workflow (recommended, no local AWS creds needed):**
Actions tab -> "Cognito demo infra (Terraform)" -> Run workflow -> pick
`action` (`plan` / `apply` / `destroy`). Outputs land in the run's job
summary: pool ID, client ID, client secret, region, base URL, plus the
speaker's own test login.

**Locally, with Terraform:**
```
cd infra/terraform
terraform init -backend-config="bucket=<TF_STATE_BUCKET>" \
                -backend-config="key=cognito/workshop/terraform.tfstate" \
                -backend-config="region=<AWS_REGION>"
terraform apply -var "aws_region=<AWS_REGION>"
```

**Locally, without Terraform:**
```
python infra/setup_cognito.py
```

Either path, tear down afterward with `infra/teardown_cognito.py` (or the
workflow's `destroy` action) -- nothing here is meant to outlive the talk.

## One-time setup (already done for this repo)

`infra/bootstrap_github_oidc.py` creates the GitHub OIDC trust + IAM role +
Terraform state bucket that the workflow depends on. It's a human-run,
run-once step, not something CI triggers itself.
