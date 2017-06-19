#!/usr/bin/env python

import os
import random
import string
import subprocess
import sys

import yaml

from boto import rds2
from boto.exception import NoAuthHandlerFound, JSONResponseError
from time import sleep

CONFIG_FILE_PATH = os.environ.get(
    'CONFIG_FILE_PATH',
    os.path.join('/', 'run', 'secrets', 'config.yml')
)

CONFIG = {}

if os.path.isfile(CONFIG_FILE_PATH):
    with open(CONFIG_FILE_PATH) as config_f:
        CONFIG.update(yaml.load(config_f))

CONFIG.setdefault('AWS_REGION', os.environ.get('AWS_REGION', 'us-east-1'))
CONFIG.setdefault('AWS_ACCESS_KEY_ID', os.environ.get('AWS_ACCESS_KEY_ID'))
CONFIG.setdefault(
    'AWS_SECRET_ACCESS_KEY',
    os.environ.get('AWS_SECRET_ACCESS_KEY')
)
CONFIG.setdefault(
    'DB_INSTANCE_CLASS',
    os.environ.get('DB_INSTANCE_CLASS', 'db.t1.micro')
)
CONFIG.setdefault('MAX_RETRIES', int(os.environ.get('MAX_RETRIES', 2)))

if not 'DB_USER' in CONFIG and 'DB_USER' in os.environ:
    CONFIG['DB_USER'] = os.environ['DB_USER']

CONFIG.setdefault('DB_PASSWORD', os.environ.get('DB_PASSWORD', ''))

def dump_postgres(db_instance, db_name, out_file_name):
    os.environ['PGPASSWORD'] = CONFIG['DB_PASSWORD']

    with open(
        '/out/%s.dump' % out_file_name, 'w'
    ) as outfile:
        return subprocess.check_call([
            'pg_dump', '-w', '-Fc',
            '-U', CONFIG.get('DB_USER', db_instance['MasterUsername']),
            '-h', db_instance['Endpoint']['Address'],
            '-p', str(db_instance['Endpoint']['Port']),
            db_name
        ], stdout=outfile)

def dump_mysql(db_instance, db_name, out_file_name):
    with open(
        '/out/%s.sql' % out_file_name, 'w'
    ) as outfile:
        return subprocess.check_call([
            'mysqldump',
            '-u', CONFIG.get('DB_USER', db_instance['MasterUsername']),
            '-p%s' % CONFIG['DB_PASSWORD'],
            '-h', db_instance['Endpoint']['Address'],
            '-P', str(db_instance['Endpoint']['Port']),
            db_name
        ], stdout=outfile)

def with_retry(func, *args, **kwargs):
    ret = None
    for x in range(CONFIG['MAX_RETRIES']):
        try:
            return func(*args, **kwargs)
        except (NoAuthHandlerFound, JSONResponseError) as e:
            ret = e
            sleep(10)
    raise ret

dump_cmds = {
    'postgres': dump_postgres,
    'mysql': dump_mysql,
}

if len(sys.argv) < 2:
    print 'Usage: %s db-instance-name [db-name ...]' % sys.argv[0]
    sys.exit(1)

conn = with_retry(rds2.connect_to_region, CONFIG['AWS_REGION'],
                  aws_access_key_id=CONFIG['AWS_ACCESS_KEY_ID'],
                  aws_secret_access_key=CONFIG['AWS_SECRET_ACCESS_KEY'])

_, db_instance_name, db_names = sys.argv[0], sys.argv[1], sys.argv[2:]

snapshots = conn.describe_db_snapshots(db_instance_name)\
        ['DescribeDBSnapshotsResponse']\
        ['DescribeDBSnapshotsResult']\
        ['DBSnapshots']

snapshots = [ s for s in snapshots if s['Status'] == 'available' ]
snapshots = sorted(snapshots, key=lambda s: s['SnapshotCreateTime'])

if len(snapshots) == 0:
    print 'No snapshots found for instance "%s"' % db_instance_name
    sys.exit(2)

latest_snapshot = snapshots[-1]
latest_snapshot_name = latest_snapshot['DBSnapshotIdentifier'].split(':')[-1]

print 'Found snapshot "%s".' % latest_snapshot['DBSnapshotIdentifier']

identifier_prefix = 'dump-{}'.format(
    "".join([random.choice(string.letters) for x in range(8)]),
)

dump_instance_identifier = '{}-{}'.format(
    identifier_prefix,
    latest_snapshot_name,
)
dump_instance_identifier = dump_instance_identifier[:63]


with_retry(
    conn.restore_db_instance_from_db_snapshot,
    dump_instance_identifier,
    latest_snapshot['DBSnapshotIdentifier'],
    publicly_accessible=True,
    db_instance_class=CONFIG['DB_INSTANCE_CLASS'],
)

print 'Launched instance "%s".' % dump_instance_identifier

try:
    TIMEOUT = 7200
    SLEEP_INTERVAL = 30

    print "Waiting for instance to become available."

    dump_instance = {}

    while TIMEOUT > 0:
        try:
            result = conn.describe_db_instances(
                dump_instance_identifier,
            )['DescribeDBInstancesResponse']['DescribeDBInstancesResult']
            dump_instance = result['DBInstances'][0]
            if dump_instance['DBInstanceStatus'] == 'available':
                break
        except JSONResponseError:
            pass

        TIMEOUT -= SLEEP_INTERVAL
        sleep(SLEEP_INTERVAL)

    if dump_instance.get('DBInstanceStatus') != 'available':
        print ('Instance "%s" did not become available within time limit. '
               'Aborting.' % dump_instance_identifier)
        exit(3)

    print "Instance is available."

    print 'Instance engine is "%s".' % dump_instance['Engine']

    if not dump_instance['Engine'] in dump_cmds:
        print "Error: Can't handle databases of this type. Aborting."
        sys.exit(4)

    if len(db_names) == 0:
        print 'Dumping "%s".' % dump_instance['DBName']
        dump_cmds[dump_instance['Engine']](
            dump_instance, dump_instance['DBName'], latest_snapshot_name
        )
    else:
        for db_name in db_names:
            print 'Dumping "%s".' % db_name
            dump_cmds[dump_instance['Engine']](
                dump_instance, db_name,
                '%s-%s' % (db_name, latest_snapshot_name)
            )

    print "Dump completed."
finally:
    with_retry(conn.delete_db_instance, dump_instance_identifier,
               skip_final_snapshot=True)

    print 'Terminated "%s".' % dump_instance_identifier
