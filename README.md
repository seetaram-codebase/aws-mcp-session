# Building an AWS-Native MCP Server

Talk materials, demo code, and the live workshop kit for the AWS meetup
session "Building an AWS-Native MCP Server: From Concept to Production".
See `session-outline.html` (or `.txt`) for the full run-of-show, corrected
facts, and sources.

## Layout

| Path | What it is |
|---|---|
| `server/` | Speaker's own reference demo -- a Personal Finance Guide MCP server (FastMCP), OAuth 2.1-protected via AWS Cognito. Built live on stage in the "Build: FastMCP server" segment. |
| `kickstart/` | What attendees clone for the live hands-on segment. Generic OAuth-protected FastMCP template + README -- add your own tool, connect your IDE, done. |
| `infra/` | Provisions the Cognito User Pool(s) that act as OAuth Authorization Server for both of the above. Two ways to run it: a one-shot `boto3` script, or Terraform (what CI uses). |
| `.github/workflows/cognito-infra.yml` | Manual (`workflow_dispatch`) pipeline that plans/applies/destroys the Terraform in `infra/terraform/`, authenticating to AWS via OIDC -- no access keys stored in this repo. |
| `session-outline.html` / `.txt`, `session-cfp.txt`, `talk.txt.txt` | The talk content itself: abstract, agenda, corrected facts, sources. |

## Running the Cognito infra

**Via the GitHub Actions workflow (recommended, no local AWS creds needed):**
Actions tab -> "Cognito demo infra (Terraform)" -> Run workflow -> pick
`mode` (`solo` for the stage demo, `workshop` for the shared attendee pool)
and `action` (`plan` / `apply` / `destroy`). Outputs land in the run's job
summary.

**Locally, with Terraform:**
```
cd infra/terraform
terraform init -backend-config="bucket=<TF_STATE_BUCKET>" \
                -backend-config="key=cognito/solo/terraform.tfstate" \
                -backend-config="region=<AWS_REGION>"
terraform apply -var-file=solo.tfvars -var "aws_region=<AWS_REGION>"
```

**Locally, without Terraform:**
```
python infra/setup_cognito.py              # solo demo pool
python infra/setup_cognito.py --workshop   # shared workshop pool
```

Either path, tear down afterward with `infra/teardown_cognito.py` (or the
workflow's `destroy` action) -- nothing here is meant to outlive the talk.

## One-time setup (already done for this repo)

`infra/bootstrap_github_oidc.py` creates the GitHub OIDC trust + IAM role +
Terraform state bucket that the workflow depends on. It's a human-run,
run-once step, not something CI triggers itself.
