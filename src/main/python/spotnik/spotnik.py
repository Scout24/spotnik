#!/usr/bin/env python
from __future__ import print_function, absolute_import, division

import sys
import logging
logging.basicConfig(level=logging.INFO)

import boto3
from pprint import pformat

EC2 = boto3.client('ec2', region_name="eu-west-1")
EC2.describe_instance = lambda instance_id: EC2.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]
AUTOSCALING = boto3.client('autoscaling', region_name="eu-west-1")
AUTOSCALING.describe_launch_configuration = lambda launch_config_name: AUTOSCALING.describe_launch_configurations(LaunchConfigurationNames=[launch_config_name])['LaunchConfigurations'][0]


def _boto_tags_to_dict(tags):
    """Convert the Tags in boto format into a usable dict

    [{'Key': 'foo', 'Value': 'bar'}, {'Key': 'ham', 'Value': 'spam'}]
    is translated to
    {'foo': 'bar', 'ham': 'spam'}
    """
    return {item['Key']: item['Value'] for item in tags}


def generate_launch_specification(launch_config, instance_to_replace):
    iam_profile_lc = launch_config['IamInstanceProfile']
    if iam_profile_lc.startswith('arn:aws:'):
        iam_profile = {'Arn': iam_profile_lc}
    else:
        iam_profile = {'Name': iam_profile_lc}

    launch_specification = {
        'ImageId': launch_config['ImageId'],
        'UserData': launch_config['UserData'],  # FIXME: test empty userdata
        # FIXME: make dynamic
        'InstanceType': "m4.large", #launch_config['InstanceType'],
        'Placement': {'AvailabilityZone': instance_to_replace['Placement']['AvailabilityZone']},
        'IamInstanceProfile': iam_profile,
        'Monitoring': dict(launch_config['InstanceMonitoring']),
        'NetworkInterfaces': get_network_specification(
                launch_config, instance_to_replace),

        # autospotter says that KernelId and RamdiskId should not be copied.

        # Fixme: may need some conversion
        'BlockDeviceMappings': launch_config['BlockDeviceMappings']
        }

    if launch_config.get("KeyName"):
        # Needed to support instances without any SSH key.
        launch_specification["KeyName"] = launch_config['KeyName']
    if launch_config.get('EbsOptimized'):
        launch_specification["EbsOptimized"] = launch_config['EbsOptimized']

    return launch_specification


def get_network_specification(launch_config, instance_to_replace):
    # FIXME: support multiple interfaces
    return [{
        'DeviceIndex': 0,
        # FIXME: support multiple groups
        'Groups': [instance_to_replace['NetworkInterfaces'][0]['Groups'][0]['GroupId']],
        'SubnetId': instance_to_replace['NetworkInterfaces'][0]['SubnetId'],
        'AssociatePublicIpAddress': launch_config['AssociatePublicIpAddress']
    }]


class ReplacementPolicy(object):
    def __init__(self, asg):
        self.asg = asg
        self.asg_name = asg['AutoScalingGroupName']
        self.asg_tags = _boto_tags_to_dict(asg['Tags'])
        self.on_demand_instances = None

        # Keep at least this many on-demand instances in the ASG.
        self.min_on_demand = int(self.asg_tags.get('spotnik-min-on-demand-instances', 0))

    def get_instances(self):
        instances = self.asg['Instances']
        spot_instances = []
        on_demand_instances = []
        for instance in instances:
            response = EC2.describe_instances(InstanceIds=[instance['InstanceId']])
            description = response['Reservations'][0]['Instances'][0]
            if description.get('InstanceLifecycle') == "spot":
                spot_instances.append(description)
            else:
                on_demand_instances.append(description)
        return on_demand_instances, spot_instances

    def is_replacement_needed(self):
        self.on_demand_instances, spot_instances = self.get_instances()
        num_on_demand_instances = len(self.on_demand_instances)

        msg = "Instances in ASG {asg}: {on_demand} on-demand, {spot} spot. "
        if self.min_on_demand:
            msg += " Configured to keep {min_on_demand} on-demand instances."

        if num_on_demand_instances > self.min_on_demand:
            replacement_needed = True
            msg += " One on-demand instance should be replaced."
        else:
            replacement_needed = False
            msg += " No instances will be replaced because "
            if self.min_on_demand:
                msg += ("the number of on-demand instances would "
                        "fall below the minimum.")
            else:
                msg += " all instances are already spotted."
        msg = msg.format(asg=self.asg_name, on_demand=num_on_demand_instances,
                         spot=len(spot_instances),
                         min_on_demand=self.min_on_demand)
        logging.info(msg)
        return replacement_needed

    def decide_replacement(self):
        # decide which instance to replace
        replaced_instance_details = EC2.describe_instance(self.on_demand_instances[0]['InstanceId'])
        logging.info("replaced_instance_details: %s\n", pformat(replaced_instance_details))
        # decide with what to replace it
        launch_config_name = self.asg['LaunchConfigurationName']
        launch_config = AUTOSCALING.describe_launch_configuration(launch_config_name)
        logging.info("launch_config: %s\n", pformat(launch_config))

        launch_specification = generate_launch_specification(launch_config, replaced_instance_details)
        logging.info("launch_specification: %s\n", pformat(launch_specification))
        # decide how much we want to pay

        bid_price = self.asg_tags['spotnik-bid-price']

        return launch_specification, replaced_instance_details, bid_price


class Spotnik(object):
    def __init__(self, asg):
        self.asg = asg
        self.asg_name = asg['AutoScalingGroupName']

    def get_pending_spot_resources(self):
        logging.info("Searching pending resources of ASG %r", self.asg_name)
        response = EC2.describe_spot_instance_requests(Filters=[
                {'Name': 'tag-value', 'Values': [self.asg_name]}])
        #response = EC2.describe_spot_instance_requests(Filters=[
        #        {'Name': 'tag:key=value', 'Values': ["spotnik=" + self.asg_name]}])
        requests = response['SpotInstanceRequests']

        for request in requests:
            if request['State'] not in ('open', 'active'):
                continue
            return request, request.get('InstanceId')
        return None, None

    def tag_new_instance(self, new_instance_id, old_instance):
        EC2.create_tags(Resources=[new_instance_id],
                        Tags=[old_instance['Tags']])

    @staticmethod
    def get_spotnik_asgs():
        asgs = AUTOSCALING.describe_auto_scaling_groups()['AutoScalingGroups']
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
        AUTOSCALING.update_auto_scaling_group(AutoScalingGroupName=self.asg_name, MaxSize=current_max_size + 1)
        AUTOSCALING.attach_instances(InstanceIds=[spot_instance_id],
                                     AutoScalingGroupName=self.asg_name)
        AUTOSCALING.detach_instances(InstanceIds=[instance_id],
                                     AutoScalingGroupName=self.asg_name,
                                     ShouldDecrementDesiredCapacity=True)
        AUTOSCALING.update_auto_scaling_group(AutoScalingGroupName=self.asg_name, MaxSize=current_max_size)

        EC2.terminate_instances(InstanceIds=[instance_id])

    @staticmethod
    def untag_spot_request(spot_request):
        EC2.delete_tags(Resources=[spot_request['SpotInstanceRequestId']], Tags=[{'Key': 'spotnik'}])

    def make_spot_request(self):
        policy = ReplacementPolicy(self.asg)
        if not policy.is_replacement_needed():
            return

        launch_specification, replaced_instance_details, bid_price = policy.decide_replacement()

        response = EC2.request_spot_instances(
            DryRun=False, SpotPrice=bid_price,
            LaunchSpecification=launch_specification)

        spot_request_id = response['SpotInstanceRequests'][0]['SpotInstanceRequestId']
        logging.info("New spot request %r for ASG %r", spot_request_id, self.asg_name)

        spot_request_tags = [
            {'Key': 'spotnik', 'Value': self.asg['AutoScalingGroupName']},
            {'Key': 'spotnik-will-replace', 'Value': replaced_instance_details['InstanceId']}]
        EC2.create_tags(Resources=[spot_request_id], Tags=spot_request_tags)


def main():
    spotnik_asgs = Spotnik.get_spotnik_asgs()
    #logging.info("will process these ASGs: %s", spotnik_asgs)
    # TODO: one thread per ASG
    for asg in spotnik_asgs:
        spotnik = Spotnik(asg)

        logging.info("Processing ASG %r: \n%s\n", spotnik.asg_name, pformat(asg))
        spot_request, spot_instance_id = spotnik.get_pending_spot_resources()
        if spot_instance_id:
            # TOOD is instance fully running yet?
            logging.info("Instance %r is ready to be attached to ASG %r", spot_instance_id, spotnik.asg_name)
            spotnik.attach_spot_instance(spot_instance_id, spot_request)
            spotnik.untag_spot_request(spot_request)
        elif spot_request:
            logging.info("ASG %r has pending spot request %r.", spotnik.asg_name, spot_request['SpotInstanceRequestId'])
            # Amazon processing our request, but no instance yet
            continue
        else:
            spotnik.make_spot_request()


if __name__ == "__main__":
    main()
    sys.exit(0)
