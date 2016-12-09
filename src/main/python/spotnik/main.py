#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import boto3
import logging
import sys
import threading
from pprint import pformat

from .spotnik import Spotnik

_ERROR_IN_MAIN = False


def handler(*_):
    formatter = logging.Formatter(fmt="%(asctime)-15s %(levelname)s - %(name)s - %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)

    main()


def get_aws_region_names():
    ec2_client = boto3.client('ec2', region_name='eu-west-1')
    return [endpoint['RegionName'] for endpoint in ec2_client.describe_regions()['Regions']]


def main():
    logger = logging.getLogger('spotnik')
    logger.setLevel(logging.INFO)

    regional_threads = []
    for region_name in get_aws_region_names():
        logger.info("Starting thread for AWS region %s", region_name)
        regional_thread = threading.Thread(target=run_regional_thread, args=(region_name,))
        regional_thread.start()
        regional_threads.append(regional_thread)

    for regional_thread in regional_threads:
        regional_thread.join()

    if _ERROR_IN_MAIN:
        raise Exception("One of the worker threads failed")


def run_regional_thread(region_name):
    logger = logging.getLogger("spotnik." + region_name)
    try:
        spotnik_asgs = Spotnik.get_spotnik_asgs(region_name)
        logger.info("Found %d spotnik ASGs", len(spotnik_asgs))

        asg_threads = []
        for asg in spotnik_asgs:
            asg_thread = threading.Thread(target=run_asg_thread, args=(region_name, asg))
            asg_thread.start()
            asg_threads.append(asg_thread)

        for asg_thread in asg_threads:
            asg_thread.join()
    except Exception:
        logger.exception("Thread failed:")
        global _ERROR_IN_MAIN
        _ERROR_IN_MAIN = True


def run_asg_thread(region_name, asg):
    logger = logging.getLogger("spotnik.%s.%s" % (region_name, asg['AutoScalingGroupName']))
    try:
        spotnik = Spotnik(region_name, asg, logger=logger)

        logger.info("Processing ASG with this config: \n%s", pformat(asg))
        spot_request, spot_instance_id = spotnik.get_pending_spot_resources()
        if spot_instance_id:
            logger.info("Instance %r is ready to be attached to ASG", spot_instance_id)
            spotnik.attach_spot_instance(spot_instance_id, spot_request)
            spotnik.untag_spot_request(spot_request)
        elif spot_request:
            logger.info("ASG has pending spot request %r.", spot_request['SpotInstanceRequestId'])
            # Amazon processing our request, but no instance yet
            return
        else:
            spotnik.make_spot_request()
    except Exception:
        logger.exception("Thread failed:")
        global _ERROR_IN_MAIN
        _ERROR_IN_MAIN = True


if __name__ == "__main__":
    main()
    sys.exit(0)
