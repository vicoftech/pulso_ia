# scripts/initial_run.py
import boto3
import json
import os
import sys

def main():
    region  = os.environ.get("AWS_REGION", "us-east-1")
    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile, region_name=region)
    sfn     = session.client("stepfunctions")

    machines = sfn.list_state_machines()["stateMachines"]
    arn = next(
        (m["stateMachineArn"] for m in machines if "pulso" in m["name"].lower()),
        None
    )
    if not arn:
        print("No se encontro la State Machine de Pulso IA.")
        print("Asegurate de haber ejecutado terraform apply primero.")
        sys.exit(1)

    print(f"State Machine encontrada: {arn}")
    print("Iniciando carga inicial (ultimos 10 dias)...")
    response = sfn.start_execution(
        stateMachineArn=arn,
        input=json.dumps({
            "sources": ["arxiv", "producthunt", "github", "rss"],
            "lookback_hours": 240,
            "initial_run": True
        })
    )
    print(f"Ejecucion iniciada: {response['executionArn']}")
    print("Monitorear en AWS Console -> Step Functions")

if __name__ == "__main__":
    main()
