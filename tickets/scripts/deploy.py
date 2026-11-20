"""Deploy with Python's standard library and AWS CLI v2. No credentials in files.

Usage: python scripts/deploy.py [--profile default] [--region us-east-1]
       python scripts/deploy.py --role-arn arn:aws:iam::123456789012:role/LabRole
       python scripts/deploy.py --package-only
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.deploy'


class AwsError(RuntimeError):
    def __init__(self, message):
        super().__init__(message)
        match = re.search(r'\((\w+)\)', message)
        self.code = match.group(1) if match else ''


def package():
    BUILD.mkdir(exist_ok=True)
    archive = BUILD / 'ritmo.zip'
    files = [ROOT / 'backend' / name for name in ('app.py', 'store.py')]
    files.extend(sorted((ROOT / 'backend' / 'web').glob('*')))
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as out:
        for file in files:
            if file.is_file():
                out.write(file, str(file.relative_to(ROOT / 'backend')).replace('\\', '/'))
    print(f'Paquete creado: {archive} ({archive.stat().st_size:,} bytes)', flush=True)
    return archive


class Deployer:
    def __init__(self, args):
        self.args = args
        self.prefix = ['aws', '--region', args.region, '--no-cli-pager', '--output', 'json']
        if args.profile:
            self.prefix.extend(['--profile', args.profile])
        self.env = os.environ.copy()
        self.env.update(AWS_PAGER='', AWS_CLI_AUTO_PROMPT='off', AWS_RETRY_MODE='standard', AWS_MAX_ATTEMPTS='3')
        self.state = {}
        self.state_path = None

    def aws(self, *command, payload=None, timeout=180):
        cmd = self.prefix + list(command)
        if payload is not None:
            # Infrastructure values only; credentials never enter command arguments.
            cmd += ['--cli-input-json', json.dumps(payload)]
        try:
            result = subprocess.run(cmd, env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError('Instala AWS CLI v2 antes de desplegar.') from None
        if result.returncode:
            raise AwsError(result.stderr.strip() or 'AWS CLI terminó con un error.')
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def save(self, **values):
        self.state.update(values)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.state_path)

    def role(self, account, table_arn):
        if self.args.role_arn:
            return self.args.role_arn
        try:
            # AWS Academy Learner Labs commonly require this preexisting execution role.
            lab = self.aws('iam', 'get-role', '--role-name', 'LabRole')['Role']['Arn']
            print('Usando el rol existente LabRole.', flush=True)
            return lab
        except AwsError as exc:
            if exc.code not in ('NoSuchEntity', 'AccessDenied', 'AccessDeniedException'):
                raise
        name = self.args.name + '-lambda-role'
        try:
            arn = self.aws('iam', 'get-role', '--role-name', name)['Role']['Arn']
        except AwsError as exc:
            if exc.code != 'NoSuchEntity':
                raise RuntimeError('No se pudo consultar IAM. Usa --role-arn con un rol existente que pueda acceder a DynamoDB y CloudWatch.') from exc
            arn = self.aws('iam', 'create-role', payload={'RoleName': name, 'AssumeRolePolicyDocument': json.dumps({'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}), 'Tags': [{'Key': 'Application', 'Value': self.args.name}]})['Role']['Arn']
        log_arn = f'arn:aws:logs:{self.args.region}:{account}:log-group:/aws/lambda/{self.args.name}'
        policy = {'Version': '2012-10-17', 'Statement': [
            {'Effect': 'Allow', 'Action': ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:DeleteItem', 'dynamodb:Query'], 'Resource': table_arn},
            {'Effect': 'Allow', 'Action': ['logs:CreateLogGroup'], 'Resource': log_arn + ':*'},
            {'Effect': 'Allow', 'Action': ['logs:CreateLogStream', 'logs:PutLogEvents'], 'Resource': log_arn + ':*'},
        ]}
        self.aws('iam', 'put-role-policy', payload={'RoleName': name, 'PolicyName': self.args.name + '-runtime', 'PolicyDocument': json.dumps(policy)})
        return arn

    def run(self, archive):
        print('Verificando la cuenta AWS…', flush=True)
        identity = self.aws('sts', 'get-caller-identity')
        account = identity['Account']
        self.state_path = BUILD / f'{self.args.name}-{account}-{self.args.region}.json'
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.save(account=account, region=self.args.region, name=self.args.name, verified=False)
        table_name = self.args.name + '-data'
        print('Preparando DynamoDB…', flush=True)
        try:
            table = self.aws('dynamodb', 'describe-table', '--table-name', table_name)['Table']
        except AwsError as exc:
            if exc.code != 'ResourceNotFoundException':
                raise
            self.aws('dynamodb', 'create-table', payload={
                'TableName': table_name, 'BillingMode': 'PAY_PER_REQUEST',
                'AttributeDefinitions': [{'AttributeName': 'pk', 'AttributeType': 'S'}, {'AttributeName': 'sk', 'AttributeType': 'S'}],
                'KeySchema': [{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
                'Tags': [{'Key': 'Application', 'Value': self.args.name}],
            })
            self.aws('dynamodb', 'wait', 'table-exists', '--table-name', table_name, timeout=600)
            table = self.aws('dynamodb', 'describe-table', '--table-name', table_name)['Table']
        expected_schema = [{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}]
        if table['KeySchema'] != expected_schema:
            raise RuntimeError(f'La tabla {table_name} ya existe con otro esquema. Usa --name con otro nombre.')
        self.save(table_name=table_name)
        ttl = self.aws('dynamodb', 'describe-time-to-live', '--table-name', table_name)['TimeToLiveDescription']
        if ttl['TimeToLiveStatus'] == 'DISABLED':
            self.aws('dynamodb', 'update-time-to-live', payload={'TableName': table_name, 'TimeToLiveSpecification': {'Enabled': True, 'AttributeName': 'ttl'}})
        role_arn = self.role(account, table['TableArn'])
        self.save(role_arn=role_arn)
        print('Desplegando Lambda Python y frontend…', flush=True)
        config = {'FunctionName': self.args.name, 'Runtime': 'python3.13', 'Role': role_arn, 'Handler': 'app.handler', 'Timeout': 15, 'MemorySize': 256, 'Environment': {'Variables': {'TABLE_NAME': table_name}}, 'Description': 'Ritmo: tareas, historial y analitica con DynamoDB'}
        try:
            function = self.aws('lambda', 'get-function', '--function-name', self.args.name)
        except AwsError as exc:
            if exc.code != 'ResourceNotFoundException':
                raise
            function = None
        if function:
            configured_table = function['Configuration'].get('Environment', {}).get('Variables', {}).get('TABLE_NAME')
            if configured_table != table_name:
                raise RuntimeError('Ya existe una Lambda con ese nombre y otra configuración. Usa otro --name.')
            self.aws('lambda', 'update-function-code', '--function-name', self.args.name, '--zip-file', 'fileb://' + str(archive))
            self.aws('lambda', 'wait', 'function-updated-v2', '--function-name', self.args.name, timeout=600)
            function_config = self.aws('lambda', 'update-function-configuration', payload=config)
            self.aws('lambda', 'wait', 'function-updated-v2', '--function-name', self.args.name, timeout=600)
        else:
            # IAM role propagation can take a few seconds after creation.
            for attempt in range(6):
                try:
                    function_config = self.aws('lambda', 'create-function', '--zip-file', 'fileb://' + str(archive), payload={**config, 'Tags': {'Application': self.args.name}})
                    break
                except AwsError as exc:
                    if 'cannot be assumed' not in str(exc) or attempt == 5:
                        raise
                    time.sleep(5)
            self.aws('lambda', 'wait', 'function-active-v2', '--function-name', self.args.name, timeout=600)
        function_arn = function_config['FunctionArn']
        self.save(function_arn=function_arn)
        print('Configurando el enlace HTTPS…', flush=True)
        api = None
        if self.state.get('api_id'):
            api = self.aws('apigatewayv2', 'get-api', '--api-id', self.state['api_id'])
        else:
            # Recover an API created before a local interruption without duplicating it.
            matches = [item for item in self.aws('apigatewayv2', 'get-apis').get('Items', []) if item.get('Name') == self.args.name and item.get('Tags', {}).get('Application') == self.args.name]
            if len(matches) > 1:
                raise RuntimeError('Hay más de una API con este nombre. Revisa AWS antes de continuar.')
            if matches:
                api = matches[0]
        if not api:
            api = self.aws('apigatewayv2', 'create-api', payload={'Name': self.args.name, 'ProtocolType': 'HTTP', 'Target': function_arn, 'Tags': {'Application': self.args.name}, 'Description': 'Frontend y API de Ritmo, mismo origen HTTPS'})
        api_id = api['ApiId']
        endpoint = api['ApiEndpoint'].rstrip('/')
        self.save(api_id=api_id, url=endpoint + '/')
        # Explicit HTTP API payload version supports Secure HttpOnly cookies.
        integrations = self.aws('apigatewayv2', 'get-integrations', '--api-id', api_id)['Items']
        integration = next((item for item in integrations if function_arn in item.get('IntegrationUri', '')), None)
        if not integration:
            raise RuntimeError('No se encontró la integración de Lambda esperada.')
        self.aws('apigatewayv2', 'update-integration', payload={'ApiId': api_id, 'IntegrationId': integration['IntegrationId'], 'PayloadFormatVersion': '2.0', 'TimeoutInMillis': 20000})
        try:
            self.aws('lambda', 'add-permission', payload={'FunctionName': self.args.name, 'StatementId': f'apigateway-{api_id}', 'Action': 'lambda:InvokeFunction', 'Principal': 'apigateway.amazonaws.com', 'SourceArn': f'arn:aws:execute-api:{self.args.region}:{account}:{api_id}/*/*'})
        except AwsError as exc:
            if exc.code != 'ResourceConflictException':
                raise
        log_group = '/aws/lambda/' + self.args.name
        try:
            try:
                self.aws('logs', 'create-log-group', '--log-group-name', log_group)
            except AwsError as exc:
                if exc.code != 'ResourceAlreadyExistsException':
                    raise
            self.aws('logs', 'put-retention-policy', '--log-group-name', log_group, '--retention-in-days', '7')
        except AwsError as exc:
            print(f'Aviso: no se pudo ajustar la retención de logs ({exc.code}).', flush=True)
        print('Comprobando HTTP y permisos de la base de datos…', flush=True)
        for attempt in range(12):
            try:
                with urllib.request.urlopen(endpoint + '/api/health', timeout=20) as response:
                    assert json.load(response)['ok'] is True
                with urllib.request.urlopen(endpoint + '/', timeout=20) as response:
                    assert 'Ritmo' in response.read().decode('utf-8')
                # A nonexistent session must produce 401, not a DynamoDB permission error (503).
                request = urllib.request.Request(endpoint + '/api/me', headers={'Cookie': 'ticket_session=deployment-probe'})
                try:
                    urllib.request.urlopen(request, timeout=20)
                    raise RuntimeError('La API aceptó una sesión inexistente.')
                except urllib.error.HTTPError as exc:
                    if exc.code != 401:
                        raise
                break
            except (urllib.error.URLError, AssertionError):
                if attempt == 11:
                    raise
                time.sleep(5)
        print('Probando registro, tareas y persistencia…', flush=True)
        smoke = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'smoke.py'), endpoint], env=self.env)
        if smoke.returncode:
            raise RuntimeError('El despliegue existe, pero la prueba funcional falló. Revisa el error antes de darlo por terminado.')
        self.save(verified=True)
        print(f'\nAplicación publicada y verificada: {endpoint}/', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Despliega Ritmo en AWS Lambda + HTTP API + DynamoDB.')
    parser.add_argument('--region', default=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-1')
    parser.add_argument('--profile')
    parser.add_argument('--name', default='ritmo-tickets')
    parser.add_argument('--role-arn', help='Rol de ejecución existente, por ejemplo LabRole en AWS Academy.')
    parser.add_argument('--package-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,39}', args.name):
        parser.error('--name debe tener 3 a 40 caracteres: letras minúsculas, números y guiones.')
    archive = package()
    if args.package_only:
        return
    try:
        Deployer(args).run(archive)
    except (RuntimeError, subprocess.TimeoutExpired, urllib.error.URLError) as exc:
        print(f'\nNo se completó el despliegue: {exc}', file=sys.stderr)
        print('El progreso queda en .deploy/. Vuelve a ejecutar el mismo comando para continuar.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
