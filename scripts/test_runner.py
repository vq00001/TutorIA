"""Disposable smoke harness. Missing acceptance suites remain a failing standard gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
COMPOSE=ROOT/'docker'/'compose.test.yaml'
OWNER='tutoria-harness-v1'


def source_digest():
    result=hashlib.sha256()
    for folder in ('backend','tests','docker'):
        for path in sorted((ROOT/folder).rglob('*')):
            if path.is_file() and path.suffix in ('.py','.sql','.lock','.toml','.yaml') and not any(p in ('.venv','__pycache__','.pytest_cache','.ruff_cache') for p in path.parts):
                result.update(str(path.relative_to(ROOT)).replace('\\','/').encode())
                result.update(path.read_bytes())
    result.update((ROOT/'alembic.ini').read_bytes())
    return result.hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--suite',choices=['unit','integration','smoke'])
    parser.add_argument('--profile',choices=['standard','full','live-ai','performance'],default='standard')
    args=parser.parse_args()
    docker=shutil.which('docker')
    if not docker:
        print('Docker is required.',file=sys.stderr)
        return 2
    # No user DATABASE_URL, Compose variables, .env or provider credentials are inherited.
    env={k:os.environ[k] for k in ('PATH','SystemRoot','WINDIR','TEMP','TMP','USERPROFILE','HOME') if k in os.environ}
    context=os.environ.get('TUTORIA_TEST_DOCKER_CONTEXT','desktop-linux')
    base=[docker,'--context',context]
    def run(command,capture=False,timeout=120):
        return subprocess.run(command,env=env,cwd=ROOT,capture_output=capture,text=True,timeout=timeout,check=False)
    if run(base+['info'],True).returncode:
        print('Docker daemon inaccessible. No test resources created.',file=sys.stderr)
        return 2
    digest=source_digest()
    if args.prepare:
        return run(base+['build','--label','org.tutoria.source-digest='+digest,'-t','tutoria-test-runner:1.1.0','-f',str(ROOT/'docker'/'Dockerfile.tests'),str(ROOT)],timeout=900).returncode
    inspection=run(base+['image','inspect','tutoria-test-runner:1.1.0'],True)
    if inspection.returncode:
        print('Prepare immutable test tools first: scripts/test.ps1 --prepare',file=sys.stderr)
        return 2
    image=json.loads(inspection.stdout)[0]
    if image['Config'].get('Labels',{}).get('org.tutoria.source-digest')!=digest:
        print('Test image does not match this source tree. Run --prepare.',file=sys.stderr)
        return 2
    if args.profile!='standard':
        print(f'Profile {args.profile} is not implemented; refusing to report acceptance.',file=sys.stderr)
        return 2
    identifier=str(uuid4())
    project='tutoria-test-'+identifier
    path=Path(tempfile.mkdtemp(prefix=project+'-')).resolve()
    env.update(TEST_RUN_ID=identifier,TEST_NONCE=secrets.token_hex(32),TEST_DB_PASSWORD=secrets.token_hex(32),TEST_RUNNER_IMAGE=image['Id'])
    env_file=path/'empty.env'
    env_file.write_text('',encoding='utf-8')
    compose=base+['compose','-p',project,'--env-file',str(env_file),'-f',str(COMPOSE)]
    created=False
    result=2
    cleaned=True
    try:
        config=run(compose+['config','--format','json'],True)
        if config.returncode:
            print('Invalid isolated Compose configuration.',file=sys.stderr)
            return 2
        manifest=json.loads(config.stdout)
        if set(manifest['services'])!={'db-test','test-runner'}:
            raise RuntimeError('Unexpected service inventory')
        if not all(n.get('internal') for n in manifest['networks'].values()):
            raise RuntimeError('Test network must be internal')
        cpu=memory=0
        for service in manifest['services'].values():
            if any(service.get(k) for k in ('ports','container_name','privileged','network_mode','devices')):
                raise RuntimeError('Unsafe test service configuration')
            if any(v.get('type')=='bind' for v in service.get('volumes',[])):
                raise RuntimeError('Test bind mounts are forbidden')
            cpu+=float(service['cpus'])
            memory+=int(service['mem_limit'])
        if cpu>2 or memory>3*1024**3:
            raise RuntimeError('Test resource budget exceeded')
        created=True
        if run(compose+['up','-d','--wait','--wait-timeout','120','db-test'],timeout=150).returncode:
            print('Database readiness failed.',file=sys.stderr)
        else:
            command=['pytest','-p','no:cacheprovider','-q','--tb=short']
            command+=['backend/tests'] if args.suite=='unit' else ['tests/integration'] if args.suite=='integration' else ['backend/tests','tests/integration']
            result=0 if run(compose+['run','--rm','--no-deps','test-runner']+command,timeout=2700).returncode==0 else 1
            if args.suite is None and result==0:
                print('Available smoke tests passed. Standard acceptance remains incomplete: full DAT matrix, E2E, restore, resilience and security suites are missing.',file=sys.stderr)
                result=2
    except KeyboardInterrupt:
        result=130
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        print(type(exc).__name__+': test infrastructure failed.',file=sys.stderr)
        result=2
    finally:
        if created:
            try:
                cleaned=run(compose+['down','--volumes','--remove-orphans','--timeout','20'],timeout=120).returncode==0
                for kind,args_list in [('container',['ps','-aq']),('volume',['volume','ls','-q']),('network',['network','ls','-q'])]:
                    remaining=run(base+args_list+['--filter','label=org.tutoria.test.run='+identifier],True)
                    if remaining.returncode or remaining.stdout.strip():
                        print('Cleanup could not verify '+kind+' absence for run '+identifier,file=sys.stderr)
                        cleaned=False
            except (OSError,subprocess.TimeoutExpired):
                cleaned=False
        if cleaned and path.parent==Path(tempfile.gettempdir()).resolve() and path.name.startswith(project+'-') and not path.is_symlink():
            shutil.rmtree(path)
        else:
            cleaned=False
        print(json.dumps(dict(runId=identifier,profile=args.profile,suite=args.suite or 'standard',sourceDigest=digest,testExit=result,cleanup='verified' if cleaned else 'pending')))
    return result if cleaned else 3


if __name__=='__main__':
    sys.exit(main())
