#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import boto3
import logging
import sys
import threading
from pprint import pformat

from .spotnik import Spotnik


def handler(*_):
    formatter = logging.Formatter(fmt="%(asctime)-15s %(levelname)s - %(name)s - %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)

    main()


def main():
    logger = logging.getLogger('spotnik')
    logger.setLevel(logging.INFO)

    ec2_client = boto3.client('ec2', region_name='eu-west-1')
    aws_region_names = [endpoint['RegionName'] for endpoint in ec2_client.describe_regions()['Regions']]
    for region_name in aws_region_names:
        logger.info("Starting thread for AWS region %s", region_name)
        regional_thread = threading.Thread(target=run_regional_thread, args=(region_name,))
        regional_thread.start()


def run_regional_thread(region_name):
    logger = logging.getLogger("spotnik." + region_name)
    spotnik_asgs = Spotnik.get_spotnik_asgs(region_name)
    logger.info("Found %d spotnik ASGs", len(spotnik_asgs))

    for asg in spotnik_asgs:
        asg_thread = threading.Thread(target=run_asg_thread, args=(region_name, asg))
        asg_thread.start()


def run_asg_thread(region_name, asg):
    logger = logging.getLogger("spotnik.%s.%s" % (region_name, asg['AutoScalingGroupName']))
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


if __name__ == "__main__":
    main()
    sys.exit(0)