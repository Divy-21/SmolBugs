#!/usr/bin/env bash
# Deploys cloud/ to AWS: ECR image -> ECS Express Mode (HTTPS URL) -> RDS Postgres.
#
#   cd cloud && AWS_PROFILE=hackathon REGION=ca-central-1 bash deploy/aws-deploy.sh
#
# Every step checks before it creates, so re-running it is safe; a re-run after
# a code change just builds, pushes and rolls out a new image.
# The first run waits ~10 minutes for RDS. Nothing secret is printed: the
# database URL and device key go straight into Secrets Manager.
set -euo pipefail
# Git Bash on Windows rewrites arguments that start with "/" into Windows paths
# ("/api/health" became "C:/Program Files/Git/api/health"). Turn that off.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"

REGION="${REGION:-ca-central-1}"
APP="smolbugs-cloud"
DB_ID="smolbugs-db"
export AWS_PAGER=""
aws() { command aws --region "$REGION" "$@"; }
say() { printf '\n== %s\n' "$*"; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
say "Account $ACCOUNT, region $REGION"

# --- 1. Image -------------------------------------------------------------------
say "ECR repository"
aws ecr describe-repositories --repository-names "$APP" >/dev/null 2>&1 ||
  aws ecr create-repository --repository-name "$APP" >/dev/null
REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$APP"
TAG=$(git rev-parse --short HEAD)$(git diff --quiet -- . || echo "-dirty")

say "Build and push $REPO:$TAG"
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
docker build --platform linux/amd64 -t "$REPO:$TAG" .
docker push "$REPO:$TAG"

# --- 2. IAM roles that Express Mode needs ---------------------------------------
role() { # name, trusted service, managed policy ARN
  if ! aws iam get-role --role-name "$1" >/dev/null 2>&1; then
    aws iam create-role --role-name "$1" --assume-role-policy-document \
      "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"$2\"},\"Action\":\"sts:AssumeRole\"}]}" >/dev/null
  fi
  aws iam attach-role-policy --role-name "$1" --policy-arn "$3"
}
say "IAM roles"
role ecsTaskExecutionRole ecs-tasks.amazonaws.com arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
role ecsInfrastructureRoleForExpressServices ecs.amazonaws.com arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
# The task needs to read our two secrets at start-up.
aws iam put-role-policy --role-name ecsTaskExecutionRole --policy-name smolbugs-secrets --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"arn:aws:secretsmanager:$REGION:$ACCOUNT:secret:smolbugs/*\"}]}"
EXEC_ROLE="arn:aws:iam::$ACCOUNT:role/ecsTaskExecutionRole"
INFRA_ROLE="arn:aws:iam::$ACCOUNT:role/ecsInfrastructureRoleForExpressServices"

# --- 3. Database: private RDS in the default VPC --------------------------------
say "Network"
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
CIDR=$(aws ec2 describe-vpcs --vpc-ids "$VPC" --query 'Vpcs[0].CidrBlock' --output text)
DB_SG=$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$VPC" Name=group-name,Values=smolbugs-db \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$DB_SG" = "None" ]; then
  DB_SG=$(aws ec2 create-security-group --vpc-id "$VPC" --group-name smolbugs-db \
    --description "SmolBugs Postgres: reachable only from inside the VPC" --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --group-id "$DB_SG" --protocol tcp --port 5432 --cidr "$CIDR" >/dev/null
fi

secret_exists() { aws secretsmanager describe-secret --secret-id "$1" >/dev/null 2>&1; }

say "RDS Postgres ($DB_ID)"
if ! aws rds describe-db-instances --db-instance-identifier "$DB_ID" >/dev/null 2>&1; then
  # Saved before the database exists, so a failed run never loses it.
  secret_exists smolbugs/db-password ||
    aws secretsmanager create-secret --name smolbugs/db-password --secret-string "$(openssl rand -hex 24)" >/dev/null
  aws rds create-db-instance --db-instance-identifier "$DB_ID" \
    --engine postgres --engine-version 16 --db-instance-class db.t4g.micro \
    --allocated-storage 20 --storage-type gp3 --db-name smolbugs \
    --master-username smolbugs \
    --master-user-password "$(aws secretsmanager get-secret-value --secret-id smolbugs/db-password --query SecretString --output text)" \
    --vpc-security-group-ids "$DB_SG" --no-publicly-accessible \
    --backup-retention-period 1 --no-multi-az >/dev/null
fi
say "Waiting for RDS to be available (first run: ~10 min)"
aws rds wait db-instance-available --db-instance-identifier "$DB_ID"
DB_HOST=$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].Endpoint.Address' --output text)

say "Secrets"
if ! secret_exists smolbugs/database-url; then
  PW=$(aws secretsmanager get-secret-value --secret-id smolbugs/db-password --query SecretString --output text)
  aws secretsmanager create-secret --name smolbugs/database-url --secret-string \
    "postgresql://smolbugs:$PW@$DB_HOST:5432/smolbugs?schema=public&sslmode=require&sslaccept=accept_invalid_certs" >/dev/null
  unset PW
fi
secret_exists smolbugs/device-keys ||
  aws secretsmanager create-secret --name smolbugs/device-keys --secret-string "$(openssl rand -hex 16)" >/dev/null
DB_URL_ARN=$(aws secretsmanager describe-secret --secret-id smolbugs/database-url --query ARN --output text)
KEYS_ARN=$(aws secretsmanager describe-secret --secret-id smolbugs/device-keys --query ARN --output text)

# --- 4. The service --------------------------------------------------------------
say "Logs and cluster"
aws logs create-log-group --log-group-name "/ecs/$APP" 2>/dev/null || true
aws ecs create-cluster --cluster-name default >/dev/null

CONTAINER=$(cat <<JSON
{"image":"$REPO:$TAG","containerPort":3000,
 "awsLogsConfiguration":{"logGroup":"/ecs/$APP","logStreamPrefix":"ecs"},
 "secrets":[{"name":"DATABASE_URL","valueFrom":"$DB_URL_ARN"},{"name":"DEVICE_KEYS","valueFrom":"$KEYS_ARN"}]}
JSON
)
SERVICE_ARN="arn:aws:ecs:$REGION:$ACCOUNT:service/default/$APP"
if aws ecs describe-express-gateway-service --service-arn "$SERVICE_ARN" >/dev/null 2>&1; then
  say "Rolling out $TAG"
  aws ecs update-express-gateway-service --service-arn "$SERVICE_ARN" --primary-container "$CONTAINER" \
    --monitor-resources DEPLOYMENT --monitor-mode TEXT-ONLY
else
  say "Creating Express Mode service $APP"
  aws ecs create-express-gateway-service --service-name "$APP" --cluster default \
    --execution-role-arn "$EXEC_ROLE" --infrastructure-role-arn "$INFRA_ROLE" \
    --health-check-path /api/health --cpu 512 --memory 1024 \
    --scaling-target minTaskCount=1,maxTaskCount=2 \
    --primary-container "$CONTAINER" \
    --monitor-resources DEPLOYMENT --monitor-mode TEXT-ONLY
fi

say "Service URL"
aws ecs describe-express-gateway-service --service-arn "$SERVICE_ARN" --output json | grep -o 'https\?://[^"]*ecs\.on\.aws[^"]*' | sort -u || true
echo "Device key: aws secretsmanager get-secret-value --region $REGION --secret-id smolbugs/device-keys --query SecretString --output text"
