from __future__ import print_function, absolute_import, division

import random
import re
from pprint import pformat
from datetime import datetime

from .util import _boto_tags_to_dict


def generate_launch_specification(launch_config, instance_to_replace, new_instance_type=None):
    new_instance_type = new_instance_type or launch_config['InstanceType']
    iam_profile_lc = launch_config['IamInstanceProfile']
    if iam_profile_lc.startswith('arn:aws:'):
        iam_profile = {'Arn': iam_profile_lc}
    else:
        iam_profile = {'Name': iam_profile_lc}

    launch_specification = {
        'ImageId': launch_config['ImageId'],
        'UserData': launch_config['UserData'],  # FIXME: test empty userdata
        'InstanceType': new_instance_type,
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
    def __init__(self, asg, spotnik):
        self.asg = asg
        self.asg_name = asg['AutoScalingGroupName']
        self.asg_tags = _boto_tags_to_dict(asg['Tags'])
        self.on_demand_instances = None

        self.spotnik = spotnik
        self.ec2_client = spotnik.ec2_client
        self.logger = spotnik.logger

        # Keep at least this many on-demand instances in the ASG.
        self.min_on_demand = int(self.asg_tags.get('spotnik-min-on-demand-instances', 0))

    def get_instances(self):
        instances = self.asg['Instances']
        spot_instances = []
        on_demand_instances = []
        for instance in instances:
            response = self.ec2_client.describe_instances(InstanceIds=[instance['InstanceId']])
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
        self.logger.info(msg)
        if not replacement_needed:
            return False

        for instance in self.on_demand_instances:
            if self.should_instance_be_replaced_now(instance):
                self.logger.info("Found an instance that is old enough for replacement")
                return True
        self.logger.info("None of the instances is old enough for replacement")
        return False

    @staticmethod
    def should_instance_be_replaced_now(instance):
        """Return True if given instance should be replaced right now

        EC2 instances are paid by the hour. Partial hours count as full hours.
        Therefor, an instance that has been running for 5 minutes should not
        be replaced, but run for another ~40 minutes.
        """
        minutes_over_hour = (datetime.utcnow().minute - instance['LaunchTime'].minute) % 60
        return 45 < minutes_over_hour < 55

    def _decide_instance_type(self):
        spotnik_instance_type = self.asg_tags.get('spotnik-instance-type', '')

        # Allow both comma and/or space separated instance types.
        instance_types = re.split(",? *", spotnik_instance_type)
        return random.choice(instance_types) or None

    def decide_replacement(self):
        # decide which instance to replace
        replaced_instance_details = self.spotnik.describe_instance(self.on_demand_instances[0]['InstanceId'])
        self.logger.info("replaced_instance_details: %s\n", pformat(replaced_instance_details))

        # decide with what to replace it
        launch_config_name = self.asg['LaunchConfigurationName']
        launch_config = self.spotnik.describe_launch_configuration(launch_config_name)
        self.logger.info("launch_config: %s\n", pformat(launch_config))

        instance_type = self._decide_instance_type()
        launch_specification = generate_launch_specification(launch_config, replaced_instance_details,
                                                             new_instance_type=instance_type)
        self.logger.info("launch_specification: %s\n", pformat(launch_specification))

        # decide how much we want to pay
        bid_price = self.asg_tags['spotnik-bid-price']

        return launch_specification, replaced_instance_details, bid_price
