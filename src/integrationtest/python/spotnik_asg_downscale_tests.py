#!/usr/bin/env python
"""
Assume the ASG scales down and an on-demand instance gets terminated.
This could be exactly the instance that Spotnik wants to replace, and
a spot instance is request already pending.

In this situation, Spotnik should terminate the new spot instance.
While not the most cost efficient, it is a simple and clean solution.
"""
from __future__ import print_function, absolute_import, division

import logging
import time
import unittest2

from pils import retry

from spotnik.spotnik import ReplacementPolicy
from spotnik_tests_base import SpotnikTestsBase
from spotnik.util import _boto_tags_to_dict


class SpotnikTestsASGDownscale(SpotnikTestsBase):
    stack_config = "src/integrationtest/integrationtest_asg_downscale.yaml"
    stack_name = "SpotnikTestASGDownscale"

    def test_spotnik_during_downscale(self):
        self.create_application_stack()

        # Normally, spotnik only replaces instances that have been running
        # for 45 to 55 minutes. This would make the integration test too
        # long, so deactivate this feature.
        ReplacementPolicy.should_instance_be_replaced_now = lambda x, y: True

        self.assert_spotnik_request_instances(1)

        _, _, asg_name = self.get_cf_output()
        instance_to_replace, spot_instance = self.get_instance_that_will_be_replaced(asg_name)
        print("Spotnik wants to replace %s, terminating it." % instance_to_replace)

        new_min_size = 1
        self.autoscaling.update_auto_scaling_group(
                AutoScalingGroupName=asg_name, MinSize=new_min_size)
        self.autoscaling.detach_instances(InstanceIds=[instance_to_replace],
                                          AutoScalingGroupName=asg_name,
                                          ShouldDecrementDesiredCapacity=True)

        # Allow some time for spot request to be fullfilled and old instance
        # to shut down.
        time.sleep(60)

        # Next run of Spotnik will try to replace $instance_to_replace, but
        # the DetachInstances() call will fail, because the instance is
        # already gone. Spotnik must...

        try:
            # ...not fail in this situation and ...
            self.assert_spotnik_request_instances(-1)
        finally:
            self.ec2.terminate_instances(InstanceIds=[instance_to_replace])

        # ... shut down the spot instance and ...
        self.assert_instance_shutdown(spot_instance)

        # ... set the desired/min/max capacity back to their original values:
        response = self.autoscaling.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name])
        asg = response['AutoScalingGroups'][0]
        self.assertEqual(asg['MinSize'], new_min_size)
        self.assertEqual(asg['MaxSize'], 5)
        self.assertEqual(asg['DesiredCapacity'], 2)

        self.delete_application_stack()

    @retry(attempts=10, delay=3)
    def assert_instance_shutdown(self, instance_id):
        response = self.ec2.describe_instances(InstanceIds=[instance_id])
        description = response['Reservations'][0]['Instances'][0]
        state = description['State']['Name']
        self.assertIn(state, ('shutting-down', 'terminated'))

    @retry(attempts=30, delay=3)
    def get_instance_that_will_be_replaced(self, asg_name):
        response = self.ec2.describe_spot_instance_requests()
        spot_requests = response['SpotInstanceRequests']

        for request in spot_requests:
            tags = _boto_tags_to_dict(request.get('Tags', {}))
            if asg_name in tags.values():
                instance_to_replace = tags.get('spotnik-will-replace')
                logging.info("Found instance %s that will be replaced.",
                             instance_to_replace)
                return instance_to_replace, request['InstanceId']
        raise Exception("No spot request found for ASG %r." % asg_name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARN)
    LOGGER = logging.getLogger('spotnik.' + SpotnikTestsASGDownscale.region_name)
    LOGGER.setLevel(logging.INFO)
    unittest2.main()
