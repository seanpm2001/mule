from celery.task import task
from celery.worker.control import Panel
from mule import conf

import os
import subprocess
import shlex
import time

@Panel.register
def mule_provision(panel, build_id):
    """
    This task has two jobs:

    1. Leaves the default Mule queue, and joins a new build-specific queue.

    2. Ensure that we're bootstrapped for this build.

       This includes:
         - Doing a git fetch
         - Setting up a virtualenv
         - Building our DB
    """
    queue_name = '%s-%s' % (conf.BUILD_QUEUE_PREFIX, build_id)

    cset = panel.consumer.task_consumer
    
    if conf.DEFAULT_QUEUE not in [q.name for q in cset.queues]:
        return {"fail": "worker is already in use"}
    
    cset.cancel_by_queue(conf.DEFAULT_QUEUE)
    
    declaration = dict(queue=queue_name, exchange_type='direct')
    queue = cset.add_consumer_from_dict(**declaration)
    # XXX: There's currently a bug in Celery 2.2.5 which doesn't declare the queue automatically
    channel = cset.channel
    queue(channel).declare()
    # channel = cset.connection.channel()
    # try:
    #     queue(channel).declare()
    # finally:
    #     channel.close()
    cset.consume()
    panel.logger.info("Started consuming from %r" % (declaration, ))

    return {
        "status": "ok",
        "build_id": build_id,
    }

@Panel.register
def mule_teardown(panel, build_id):
    """
    This task has two jobs:
    
    1. Run any bootstrap teardown

    2. Leaves the build-specific queue, and joins the default Mule queue.
    """
    queue_name = '%s-%s' % (conf.BUILD_QUEUE_PREFIX, build_id)

    cset = panel.consumer.task_consumer
    channel = cset.channel
    # kill all jobs in queue
    channel.queue_purge(queue=queue_name)
    # stop consuming from queue
    cset.cancel_by_queue(queue_name)
    
    queue = cset.add_consumer_from_dict(queue=conf.DEFAULT_QUEUE)
    # XXX: There's currently a bug in Celery 2.2.5 which doesn't declare the queue automatically
    queue(channel).declare()

    # start consuming from default
    cset.consume()

    panel.logger.info("Rejoined default queue")

    return {
        "status": "ok",
        "build_id": build_id,
    }


@task(ignore_result=False)
def run_test(build_id, runner, job):
    """
    Spawns a test runner and reports the result.
    """
    logger = run_test.get_logger()
    
    # TODO: we shouldnt need to do this, bash should do it
    cmd = runner.encode('utf-8').replace('$TEST', job.encode('utf-8'))

    logger.info('Job received: %s', cmd)

    # Setup our environment variables
    env = os.environ.copy()
    env['TEST'] = job.encode('utf-8')
    
    start = time.time()
    
    proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env)

    (stdout, stderr) = proc.communicate()

    proc.wait()

    stop = time.time()

    return {
        "timeStarted": start,
        "timeFinished": stop,
        "retcode": proc.returncode,
        "build_id": build_id,
        "job": job,
        "stdout": stdout,
        "stderr": stderr,
    }

    logger.info('Finished!')
