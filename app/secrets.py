import json

import boto3


def get_db_password(secret_arn: str) -> str:
    client = boto3.client("secretsmanager", region_name="ca-central-1")
    response = client.get_secret_value(SecretId=secret_arn)
    secret = json.loads(response["SecretString"])
    return str(secret["password"])
