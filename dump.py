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
CONFIG.setdefault(
    'MAX_DUMP_RETRIES',
    int(os.environ.get('MAX_DUMP_RETRIES', CONFIG['MAX_RETRIES'])),
)
CONFIG.setdefault('DB_SUBNET_GROUP_NAME', os.environ.get('DB_SUBNET_GROUP_NAME'))

if not 'DB_USER' in CONFIG and 'DB_USER' in os.environ:
    CONFIG['DB_USER'] = os.environ['DB_USER']

CONFIG.setdefault('DB_PASSWORD', os.environ.get('DB_PASSWORD', ''))

def db_credentials(db_name):
    db_config = CONFIG.get('databases', {})
    # If there is an empty databases section in the config file
    if not db_config:
        db_config = {}

    credentials = db_config.get(db_name, {})
    # If the DB name is listed but has no credentials
    if not credentials:
        credentials = {}

    return (
        credentials.get('user', CONFIG.get('DB_USER', '')),
        credentials.get('password', CONFIG.get('DB_PASSWORD', '')),
    )

def dump_postgres(db_instance, db_name, out_file_name):
    db_user, os.environ['PGPASSWORD'] = db_credentials(db_name)
    if not db_user:
        db_user = db_instance['MasterUsername']

    with open(
        '/out/%s.dump' % out_file_name, 'w'
    ) as outfile:
        return subprocess.check_call([
            'pg_dump', '-w', '-Fc',
            '-U', db_user,
            '-h', db_instance['Endpoint']['Address'],
            '-p', str(db_instance['Endpoint']['Port']),
            db_name
        ], stdout=outfile)

def dump_mysql(db_instance, db_name, out_file_name):
    db_user, db_password = db_credentials(db_name)
    if not db_user:
        db_user = db_instance['MasterUsername']

    with open(
        '/out/%s.sql' % out_file_name, 'w'
    ) as outfile:
        return subprocess.check_call([
            'mysqldump',
            '-u', db_user,
            '-p%s' % db_password,
            '-h', db_instance['Endpoint']['Address'],
            '-P', str(db_instance['Endpoint']['Port']),
            db_name
        ], stdout=outfile)

def with_retry(func, *args, **kwargs):
    ret = None
    if 'retries' in kwargs:
        retries = kwargs.pop('retries')
    else:
        retries = CONFIG['MAX_RETRIES']
    for x in range(retries):
        try:
            return func(*args, **kwargs)
        except (
            NoAuthHandlerFound,
            JSONResponseError,
            subprocess.CalledProcessError,
        ) as e:
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
    db_subnet_group_name=CONFIG['DB_SUBNET_GROUP_NAME'],
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
        if len(CONFIG.get('databases', [])) == 0:
            db_names = [dump_instance['DBName']]
        else:
            db_names = CONFIG['databases'].keys()

    for db_name in db_names:
        print 'Dumping "%s".' % db_name
        with_retry(
            dump_cmds[dump_instance['Engine']],
            dump_instance,
            db_name,
            '%s-%s' % (db_name, latest_snapshot_name),
            retries=CONFIG['MAX_DUMP_RETRIES'],
        )

    print "Dump completed."
finally:
    with_retry(conn.delete_db_instance, dump_instance_identifier,
               skip_final_snapshot=True)

    print 'Terminated "%s".' % dump_instance_identifier
