#!/usr/bin/env python

import os
import random
import string
import subprocess
import sys

from boto import rds2
from time import sleep

AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')

def dump_postgres(db_instance, out_file_name):
    if not 'PGPASSWORD' in os.environ:
        os.environ['PGPASSWORD'] = os.environ.get('DB_PASSWORD')

    with open(
        '/out/%s.dump' % out_file_name, 'w'
    ) as outfile:
        return subprocess.check_call([
            "pg_dump", "-w", "-Fc",
            "-U", db_instance['MasterUsername'],
            "-h", db_instance['Endpoint']['Address'],
            "-p", str(db_instance['Endpoint']['Port']),
            db_instance['DBName']
        ], stdout=outfile)

dump_cmds = {
    'postgres': dump_postgres,
}

conn = rds2.connect_to_region(AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID,
                              aws_secret_access_key=AWS_SECRET_ACCESS_KEY)

if len(sys.argv) != 2:
    print 'Usage: %s db-instance-name' % sys.argv[0]
    sys.exit(1)

_, db_instance_name = sys.argv

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
latest_snapshot_name = latest_snapshot['DBSnapshotIdentifier'].split(':')[1]

print 'Found snapshot "%s".' % latest_snapshot['DBSnapshotIdentifier']

dump_instance_identifier = 'dump-%s-%s' % (
    latest_snapshot_name,
    "".join([ random.choice(string.letters) for x in range(8) ])
)

conn.restore_db_instance_from_db_snapshot(
    dump_instance_identifier,
    latest_snapshot['DBSnapshotIdentifier'],
    publicly_accessible=True
)

print 'Launched instance "%s".' % dump_instance_identifier

TIMEOUT = 1200
SLEEP_INTERVAL = 30

print "Waiting for instance to become available."

while TIMEOUT > 0:
    dump_instance = conn.describe_db_instances(dump_instance_identifier)\
            ['DescribeDBInstancesResponse']\
            ['DescribeDBInstancesResult']\
            ['DBInstances'][0]

    if dump_instance['DBInstanceStatus'] == 'available':
        break

    TIMEOUT -= SLEEP_INTERVAL
    sleep(SLEEP_INTERVAL)

if dump_instance['DBInstanceStatus'] != 'available':
    print ('Instance "%s" did not become available within time limit. '
           'Aborting.' % dump_instance_identifier)
    conn.delete_db_instance(dump_instance_identifier,
                            skip_final_snapshot=True)
    exit(3)

print "Instance is available."

print 'Instance engine is "%s".' % dump_instance['Engine']

if not dump_instance['Engine'] in dump_cmds:
    print "Error: Can't handle databases of this type. Aborting."
    sys.exit(4)

print 'Dumping "%s".' % dump_instance['DBName']

dump_cmds[dump_instance['Engine']](
    dump_instance, latest_snapshot_name
)

print "Dump completed."

conn.delete_db_instance(dump_instance_identifier,
                       skip_final_snapshot=True)

print 'Terminated "%s".' % dump_instance_identifier
