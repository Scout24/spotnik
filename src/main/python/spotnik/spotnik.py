#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import sys
import threading
import logging

from pils import retry

import boto3
from pprint import pformat

from .util import _boto_tags_to_dict
from .replacement_policy import ReplacementPolicy


class Spotnik(object):
    def __init__(self, region_name, asg):
        self.asg = asg
        self.asg_name = asg['AutoScalingGroupName']

        self.ec2_client = boto3.client('ec2', region_name=region_name)
        self.asg_client = boto3.client('autoscaling', region_name=region_name)

    def describe_instance(self, instance_id):
        response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
        return response['Reservations'][0]['Instances'][0]

    def describe_launch_configuration(self, launch_config_name):
        response = self.asg_client.describe_launch_configurations(LaunchConfigurationNames=[launch_config_name])
        return response['LaunchConfigurations'][0]

    def get_pending_spot_resources(self):
        logging.info("Searching pending resources of ASG %r", self.asg_name)
        response = self.ec2_client.describe_spot_instance_requests(Filters=[
                {'Name': 'tag-value', 'Values': [self.asg_name]}])
        requests = response['SpotInstanceRequests']

        for request in requests:
            if request['State'] not in ('open', 'active'):
                continue

            instance_id = request.get('InstanceId')
            if instance_id is None:
                return request, None

            details = self.describe_instance(instance_id)
            state = details['State']['Name']
            logging.info("Found spot instance %s which is in state %s.", instance_id, state)
            if state == 'running':
                return request, instance_id
            return request, None
        return None, None

    def tag_new_instance(self, new_instance_id, old_instance):
        self.ec2_client.create_tags(Resources=[new_instance_id],
                        Tags=[old_instance['Tags']])

    @staticmethod
    def get_spotnik_asgs(region_name):
        client = boto3.client('autoscaling', region_name=region_name)
        asgs = client.describe_auto_scaling_groups()['AutoScalingGroups']
        spotnik_asgs = []
        for asg in asgs:
            tags = asg['Tags']
            tag_keys = [tag['Key'] for tag in tags]
            if 'spotnik' in tag_keys:
                spotnik_asgs.append(asg)
        return spotnik_asgs

    def attach_spot_instance(self, spot_instance_id, spot_request):
        instance_id = _boto_tags_to_dict(spot_request['Tags'])['spotnik-will-replace']

        logging.info("attaching: %r detaching: %r in ASG %r", spot_instance_id, instance_id, self.asg_name)

        # If the ASG is already at its MaxSize, we cannot attach a new instance.
        # So either
        #   - temporarily increase the MaxSize with AUTOSCALING.update_auto_scaling_group()
        #   or
        #   - detach the old instance before attaching the new one
        current_max_size = self.asg['MaxSize']
        self.asg_client.update_auto_scaling_group(AutoScalingGroupName=self.asg_name, MaxSize=current_max_size + 1)
        self.asg_client.attach_instances(InstanceIds=[spot_instance_id],
                                     AutoScalingGroupName=self.asg_name)
        self.asg_client.detach_instances(InstanceIds=[instance_id],
                                     AutoScalingGroupName=self.asg_name,
                                     ShouldDecrementDesiredCapacity=True)
        self.asg_client.update_auto_scaling_group(AutoScalingGroupName=self.asg_name, MaxSize=current_max_size)

        self.ec2_client.terminate_instances(InstanceIds=[instance_id])

    def untag_spot_request(self, spot_request):
        # Remove tags so that self.get_pending_spot_resources() does not find
        # this spot request again.
        self.ec2_client.delete_tags(Resources=[spot_request['SpotInstanceRequestId']], Tags=[{'Key': 'spotnik'}])

    def make_spot_request(self):
        policy = ReplacementPolicy(self.asg, self)
        if not policy.is_replacement_needed():
            return

        launch_specification, replaced_instance_details, bid_price = policy.decide_replacement()

        response = self.ec2_client.request_spot_instances(
            DryRun=False, SpotPrice=bid_price,
            LaunchSpecification=launch_specification)

        spot_request_id = response['SpotInstanceRequests'][0]['SpotInstanceRequestId']
        logging.info("New spot request %r for ASG %r", spot_request_id, self.asg_name)

        tags = [
            {'Key': 'spotnik', 'Value': self.asg['AutoScalingGroupName']},
            {'Key': 'spotnik-will-replace', 'Value': replaced_instance_details['InstanceId']}]
        self.tag_spot_request(spot_request_id, tags)

    @retry(attempts=3, delay=3)
    def tag_spot_request(self, spot_request_id, tags):
        self.ec2_client.create_tags(Resources=[spot_request_id], Tags=tags)


def main():
    ec2_client = boto3.client('ec2', region_name='eu-west-1')
    aws_region_names = [endpoint['RegionName'] for endpoint in ec2_client.describe_regions()['Regions']]
    for region_name in aws_region_names:
        logging.info("Starting thread for AWS region %s", region_name)
        regional_thread = threading.Thread(target=run_regional_thread, args=(region_name,))
        regional_thread.start()


def run_regional_thread(region_name):
    spotnik_asgs = Spotnik.get_spotnik_asgs(region_name)
    logging.info("Found %d spotnik ASGs in region %s",
                 len(spotnik_asgs), region_name)

    for asg in spotnik_asgs:
        asg_thread = threading.Thread(target=run_asg_thread, args=(region_name, asg))
        asg_thread.start()

def run_asg_thread(region_name, asg):
    spotnik = Spotnik(region_name, asg)

    logging.info("Processing ASG %r: \n%s\n", spotnik.asg_name, pformat(asg))
    spot_request, spot_instance_id = spotnik.get_pending_spot_resources()
    if spot_instance_id:
        logging.info("Instance %r is ready to be attached to ASG %r", spot_instance_id, spotnik.asg_name)
        spotnik.attach_spot_instance(spot_instance_id, spot_request)
        spotnik.untag_spot_request(spot_request)
    elif spot_request:
        logging.info("ASG %r has pending spot request %r.", spotnik.asg_name, spot_request['SpotInstanceRequestId'])
        # Amazon processing our request, but no instance yet
        return
    else:
        spotnik.make_spot_request()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
    sys.exit(0)
