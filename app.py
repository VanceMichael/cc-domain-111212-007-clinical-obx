import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile

from source import inspect_bytes
from sampledata import samples
from storage import Archive
from repository import ObservationStore
from transport import ReplaySource, fetch

MAX_INPUT = 16*1024*1024

def read_input(path):
    with Path(path).open('rb') as handle:
        data = handle.read(MAX_INPUT+1)
    if len(data) > MAX_INPUT:
        raise ValueError('输入超过接收限制')
    return data

def demo():
    fixtures = samples()
    name = next(key for key in fixtures if key.startswith('sample.'))
    data = fixtures[name]
    routes = {'GET /sample':[{'base64':base64.b64encode(data).decode()}]}
    with tempfile.TemporaryDirectory() as directory, ReplaySource(routes) as server:
        status, headers, raw = fetch(server.url + '/sample')
        if status != 200:
            raise ValueError('来源请求失败')
        rows = inspect_bytes(raw)
        with Archive(Path(directory)/'archive.sqlite3') as archive:
            first = archive.save('local-demo', raw, rows)
            repeated = archive.save('local-demo', raw, rows)
            archive_metrics = dict(input=name, bytes=len(raw),
                                   captures=len(archive.list()),
                                   same_capture=first==repeated, rows=rows,
                                   requests=len(server.requests))

    # 类型化采集演示：原件留存与观察记录库使用各自的临时库，
    # 覆盖首次采集、重送幂等、更正回溯、子观察撤销与隔离原因。
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory)/'observations.sqlite3'
        with ObservationStore(db) as repo:
            initial = repo.ingest('local-demo', fixtures['sample.hl7'])
            resent = repo.ingest('local-demo', fixtures['sample.hl7'])
            corrected = repo.ingest('local-demo', fixtures['correction.hl7'])
            repo.ingest('local-demo', fixtures['subids.hl7'])
            cancelled = repo.ingest('local-demo', fixtures['subid_cancel.hl7'])
            quarantined = repo.ingest('local-demo', fixtures['edge_values.hl7'])
            remote = repo.ingest('remote-demo', fixtures['alternate_escape.hl7'])
            typed = dict(
                messages=repo.message_count(),
                observations=len(repo.list_observations()),
                initial_versions=initial['new_versions'],
                resent_duplicate=resent['duplicate'],
                corrected_versions=corrected['new_versions'],
                cancelled_observations=[
                    item['identity_key'] for item in repo.list_observations()
                    if item['state'] == 'cancelled'],
                quarantined=quarantined['new_versions'],
                remote_repeated_values=remote['new_versions'],
            )
            archive_metrics['typed_collection'] = typed
        return archive_metrics

def main(argv=None):
    parser = argparse.ArgumentParser(description='来源检查与原件留存')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'capture'):
        command = commands.add_parser(name)
        command.add_argument('path')
        if name == 'capture':
            command.add_argument('--database', required=True)
            command.add_argument('--source', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--database', required=True)
    fixtures = commands.add_parser('fixtures')
    fixtures.add_argument('directory')
    commands.add_parser('demo')

    # 类型化观察采集命令
    collect = commands.add_parser('collect',
                                  help='类型化采集一条 ORU 报文到观察记录库')
    collect.add_argument('path')
    collect.add_argument('--database', required=True)
    collect.add_argument('--source', required=True)
    observations = commands.add_parser('observations',
                                       help='列出观察（不含患者姓名）')
    observations.add_argument('--database', required=True)
    observations.add_argument('--include-cancelled', action='store_true')
    history = commands.add_parser('history', help='查看观察版本与更正/撤销轨迹')
    history.add_argument('--database', required=True)
    history.add_argument('--patient-id')

    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            result = demo()
        elif args.command == 'fixtures':
            destination = Path(args.directory)
            destination.mkdir(parents=True, exist_ok=True)
            for name, raw in samples().items():
                path = destination/name
                if path.exists():
                    raise ValueError('不覆盖已有夹具文件：'+name)
                path.write_bytes(raw)
            result = dict(files=sorted(samples()))
        elif args.command == 'list':
            with Archive(args.database) as archive:
                result = archive.list()
        elif args.command == 'collect':
            raw = read_input(args.path)
            with ObservationStore(args.database) as repo:
                result = repo.ingest(args.source, raw)
        elif args.command == 'observations':
            with ObservationStore(args.database) as repo:
                result = repo.list_observations(
                    include_cancelled=args.include_cancelled)
        elif args.command == 'history':
            with ObservationStore(args.database) as repo:
                result = repo.history(patient_id=args.patient_id)
        else:
            raw = read_input(args.path)
            rows = inspect_bytes(raw)
            if args.command == 'inspect':
                result = rows
            else:
                with Archive(args.database) as archive:
                    result = dict(capture_id=archive.save(args.source,raw,rows), rows=len(rows))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps(dict(error=type(error).__name__, reason=str(error)), ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
