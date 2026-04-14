# Copiá a workium.tfvars (no commitear secretos) y aplicá:
#   terraform apply -var-file=workium.tfvars
#
# 1) ACM (consola AWS o CLI) en la MISMA región que el API (ej. us-east-1):
#    - Solicitar certificado para news.workium.ai (o *.workium.ai).
#    - Validación DNS: añadí los registros CNAME que ACM te indique en workium.ai.
# 2) Cuando el certificado quede "Issued", pegá el ARN abajo y terraform apply.
# 3) Salida custom_domain_target_domain: en tu DNS creá
#      Tipo CNAME | news | -> | (valor del output, suele ser d-xxxxx.execute-api...amazonaws.com)
#    (Si usás Cloudflare, proxy desactivado naranja o "DNS only" para TLS correcto con ACM.)
# 4) Opcional: python scripts/register_webhook.py --url "$(terraform output -raw webhook_url_custom_domain)"

link_public_base_url                      = "https://news.workium.ai"
api_gateway_custom_domain_certificate_arn = "arn:aws:acm:us-east-1:TU_ACCOUNT_ID:certificate/TU-CERT-ID"
