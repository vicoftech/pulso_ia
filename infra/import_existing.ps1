#Requires -Version 5.1
<#
  Reconstruye el estado de Terraform en esta máquina cuando los recursos ya existen
  en AWS y NO tenés terraform.tfstate (el state está en otra PC o se perdió).

  Prerrequisitos:
    - AWS CLI v2, Terraform >= 1.0
    - Perfil/credenciales con permisos de lectura (y terraform import usa los mismos)
    - Desde infra/: zips de lambdas y layer coherentes (o el próximo plan puede querer actualizar código)

  Uso (PowerShell):
    cd infra
    (En main.tf no debe haber backend "s3" {} vacío: impide import/plan sin backend.hcl.)
    terraform init
    .\import_existing.ps1 -AwsProfile asap_main -Region us-east-1

  Opcional: también importar el backend de estado remoto (bucket + lock), si ya existen:
    .\import_existing.ps1 -AwsProfile asap_main -IncludeStateBackend

  Flujo recomendado:
    1) terraform init -backend=false
    2) .\import_existing.ps1 ...
    3) terraform plan -var="aws_region=us-east-1"
    4) Creá backend.hcl y: terraform init -backend-config=backend.hcl -migrate-state
       (copia este state local a S3; no uses solo -reconfigure sin migrar o perdés el state)

  Si podés copiar terraform.tfstate desde la otra máquina a infra/, evitás todos los imports
  y solo necesitás el paso migrate-state hacia S3.
#>

param(
    [string] $ProjectName = "pulso-ia",
    [string] $Region = "us-east-1",
    [string] $AwsProfile = "default",
    [switch] $IncludeStateBackend
)

$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = $AwsProfile

function AwsJson([string[]] $CliArgs) {
    $out = & aws @CliArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "aws failed: $out" }
    return $out | ConvertFrom-Json
}

function Get-InlinePolicyName([string] $RoleName) {
    $j = AwsJson @("iam", "list-role-policies", "--role-name", $RoleName, "--region", $Region, "--output", "json")
    $names = @($j.PolicyNames)
    if ($names.Count -lt 1) { throw "Role $RoleName has no inline policies" }
    if ($names.Count -gt 1) {
        throw "Role $RoleName has multiple inline policies: $($names -join ', '). Remove extras or edit import_existing.ps1 to pick the right one."
    }
    return [string]$names[0]
}

function TfImport([string] $Address, [string] $Id) {
    Write-Host "terraform import $Address $Id"
    terraform import -var="aws_region=$Region" $Address $Id
    if ($LASTEXITCODE -ne 0) { throw "import failed: $Address" }
}

$account = (AwsJson @("sts", "get-caller-identity", "--output", "json")).Account
$stateBucket = "$ProjectName-terraform-state-$account"
$tableItems = "${ProjectName}_items"
$tableLocks = "$ProjectName-terraform-locks"

Write-Host "Account $account  Project $ProjectName  Region $Region  Profile $AwsProfile"

# --- State backend (opcional) ---
if ($IncludeStateBackend) {
    TfImport "aws_s3_bucket.terraform_state" $stateBucket
    TfImport "aws_s3_bucket_versioning.terraform_state" $stateBucket
    TfImport "aws_s3_bucket_server_side_encryption_configuration.terraform_state" $stateBucket
    TfImport "aws_s3_bucket_public_access_block.terraform_state" $stateBucket
    TfImport "aws_dynamodb_table.terraform_locks" $tableLocks
}

# --- Core stack ---
TfImport "aws_dynamodb_table.items" $tableItems

$roles = @(
    @{ tf = "aws_iam_role.fetch"; name = "$ProjectName-fetch-role" },
    @{ tf = "aws_iam_role.filter"; name = "$ProjectName-filter-role" },
    @{ tf = "aws_iam_role.publish"; name = "$ProjectName-publish-role" },
    @{ tf = "aws_iam_role.sfn"; name = "$ProjectName-sfn-role" },
    @{ tf = "aws_iam_role.scheduler"; name = "$ProjectName-scheduler-role" }
)
foreach ($r in $roles) {
    TfImport $r.tf $r.name
}

$pFetch = Get-InlinePolicyName "$ProjectName-fetch-role"
$pFilter = Get-InlinePolicyName "$ProjectName-filter-role"
$pPublish = Get-InlinePolicyName "$ProjectName-publish-role"
$pSfn = Get-InlinePolicyName "$ProjectName-sfn-role"
$pSched = Get-InlinePolicyName "$ProjectName-scheduler-role"

TfImport "aws_iam_role_policy.fetch_policy" "$ProjectName-fetch-role:$pFetch"
TfImport "aws_iam_role_policy.filter_policy" "$ProjectName-filter-role:$pFilter"
TfImport "aws_iam_role_policy.publish_policy" "$ProjectName-publish-role:$pPublish"
TfImport "aws_iam_role_policy.sfn_policy" "$ProjectName-sfn-role:$pSfn"
TfImport "aws_iam_role_policy.scheduler_policy" "$ProjectName-scheduler-role:$pSched"

$layerName = "$ProjectName-shared"
$lv = AwsJson @("lambda", "list-layer-versions", "--layer-name", $layerName, "--region", $Region, "--output", "json")
if (-not $lv.LayerVersions -or $lv.LayerVersions.Count -lt 1) { throw "No versions for layer $layerName" }
$maxVer = ($lv.LayerVersions | ForEach-Object { [int]$_.Version } | Measure-Object -Maximum).Maximum
$layerArn = (AwsJson @(
        "lambda", "get-layer-version", "--layer-name", $layerName,
        "--version-number", "$maxVer", "--region", $Region, "--output", "json"
    )).LayerVersionArn
TfImport "aws_lambda_layer_version.shared" $layerArn

TfImport "aws_lambda_function.fetch" "$ProjectName-fetch-sources"
TfImport "aws_lambda_function.filter" "$ProjectName-filter-ai-news"
TfImport "aws_lambda_function.publish" "$ProjectName-publish-telegram"

TfImport "aws_cloudwatch_log_group.fetch" "/aws/lambda/$ProjectName-fetch-sources"
TfImport "aws_cloudwatch_log_group.filter" "/aws/lambda/$ProjectName-filter-ai-news"
TfImport "aws_cloudwatch_log_group.publish" "/aws/lambda/$ProjectName-publish-telegram"

$sm = AwsJson @("stepfunctions", "list-state-machines", "--region", $Region, "--output", "json")
$arn = ($sm.stateMachines | Where-Object { $_.name -eq "$ProjectName-pipeline" } | Select-Object -First 1).stateMachineArn
if (-not $arn) { throw "State machine $ProjectName-pipeline not found" }
TfImport "aws_sfn_state_machine.pipeline" $arn

TfImport "aws_scheduler_schedule.hourly" "default/$ProjectName-hourly"

Write-Host ""
Write-Host "Listo. Ejecutá: terraform plan -var=`"aws_region=$Region`""
Write-Host "Si usás backend S3: configurá backend.hcl y luego terraform init -backend-config=backend.hcl -migrate-state"
